"""API-independent chapter files with lossless reads and atomic saves."""
import os
from pathlib import Path
import re
import tempfile


class ChapterConflictError(OSError):
    """The chapter changed on disk since it was loaded."""


class ChapterStore:
    def __init__(self, project_path):
        if not str(project_path).strip():
            raise ValueError("Please select a project directory first.")
        self.directory = Path(project_path).expanduser().resolve() / "chapters"

    def path(self, number):
        number = str(number)
        if not re.fullmatch(r"[0-9]+", number):
            raise ValueError("Chapter number must contain ASCII digits only.")
        return self.directory / f"chapter_{number}.txt"

    def list_chapters(self):
        if not self.directory.exists():
            return []
        numbers = []
        for path in self.directory.iterdir():
            match = re.fullmatch(r"chapter_([0-9]+)\.txt", path.name)
            if match and path.is_file():
                numbers.append(match.group(1))
        return sorted(numbers, key=lambda number: (int(number), number))

    def load(self, number):
        data = self.path(number).read_bytes()
        return data.decode("utf-8-sig"), data

    def save(self, number, text, expected):
        """Detect stale content, then replace atomically on the same filesystem.

        The comparison is optimistic, not a lock against concurrent writers.
        """
        path = self.path(number)
        data = text.encode("utf-8")
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=self.directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                current = path.read_bytes()
            except FileNotFoundError as exc:
                raise ChapterConflictError("Chapter was deleted on disk; refresh before saving.") from exc
            if current != expected:
                raise ChapterConflictError("Chapter changed on disk; preserve your edits before refreshing.")
            os.replace(temporary, path)
            return data
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def create(self, number, text=""):
        path = self.path(number)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Numeric aliases such as chapter_01.txt count as the same new number.
        if any(int(existing) == int(number) for existing in self.list_chapters()):
            raise FileExistsError(f"Chapter {number} already exists.")
        with path.open("xb") as stream:
            stream.write(text.encode("utf-8"))
        return number

    def delete(self, number, expected):
        path = self.path(number)
        if path.read_bytes() != expected:
            raise ChapterConflictError("Chapter changed on disk; refresh before deleting.")
        path.unlink()

    def next_number(self):
        return str(max((int(number) for number in self.list_chapters()), default=0) + 1)
