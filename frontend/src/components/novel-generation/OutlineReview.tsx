"use client";

import { useState } from "react";
import { Button } from "@heroui/react";
import {
  countOutline,
  type NovelOutline,
  type OutlineChapter,
  type OutlineVolume,
} from "@/types/novelBlueprint";

interface OutlineReviewProps {
  outline: NovelOutline;
  onChange: (outline: NovelOutline) => void;
  /** confirm=true 时创建正式卷和章节；false 时只保存用户修改后的新版本。 */
  onConfirm: (confirm: boolean) => void;
  onGenerateChapters?: (chapterIds: string[]) => void;
  readOnly?: boolean;
  saving?: boolean;
  confirming?: boolean;
  hint?: string;
}

/**
 * 功能：卷章大纲确认面板。
 *
 * 展示 AI 生成的卷章候选并允许逐章编辑；编辑后的内容作为新版本保存，
 * 只有显式确认才会创建正式卷和章节。
 */
export default function OutlineReview({
  outline,
  onChange,
  onConfirm,
  onGenerateChapters,
  readOnly = false,
  saving = false,
  confirming = false,
  hint,
}: OutlineReviewProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { volumeCount, chapterCount } = countOutline(outline);

  const patchVolume = (volumeIndex: number, change: Partial<OutlineVolume>) => {
    const volumes = outline.volumes.map((volume, index) =>
      index === volumeIndex ? { ...volume, ...change } : volume
    );
    onChange({ ...outline, volumes });
  };

  const patchChapter = (
    volumeIndex: number,
    chapterIndex: number,
    change: Partial<OutlineChapter>
  ) => {
    const volumes = outline.volumes.map((volume, index) => {
      if (index !== volumeIndex) return volume;
      return {
        ...volume,
        chapters: volume.chapters.map((chapter, i) =>
          i === chapterIndex ? { ...chapter, ...change } : chapter
        ),
      };
    });
    onChange({ ...outline, volumes });
  };

  const removeChapter = (volumeIndex: number, chapterIndex: number) => {
    const volumes = outline.volumes.map((volume, index) =>
      index === volumeIndex
        ? { ...volume, chapters: volume.chapters.filter((_, i) => i !== chapterIndex) }
        : volume
    );
    onChange({ ...outline, volumes });
  };

  const addChapter = (volumeIndex: number) => {
    const volumes = outline.volumes.map((volume, index) =>
      index === volumeIndex
        ? {
            ...volume,
            chapters: [
              ...volume.chapters,
              {
                title: `第 ${volume.chapters.length + 1} 章`,
                summary: "",
                goals: [],
                conflicts: [],
                character_ids: [],
                faction_ids: [],
                setting_card_ids: [],
                power_changes: [],
                foreshadowing: [],
                chapter_id: null,
              },
            ],
          }
        : volume
    );
    onChange({ ...outline, volumes });
  };

  const confirmedChapterIds = outline.volumes
    .flatMap((volume) => volume.chapters)
    .map((chapter) => chapter.chapter_id)
    .filter((id): id is string => Boolean(id));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-surface p-4">
        <div>
          <div className="text-sm font-medium text-foreground">卷章大纲预览</div>
          <div className="mt-0.5 text-xs text-muted">
            共 {volumeCount} 卷 / {chapterCount} 章 · 大纲版本 v{outline.version}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onPress={() => onConfirm(false)}
            isDisabled={readOnly || saving}
          >
            {saving ? "保存中…" : "保存草稿"}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onPress={() => onConfirm(true)}
            isDisabled={readOnly || confirming || chapterCount === 0}
          >
            {confirming ? "确认中…" : "确认并生成卷章"}
          </Button>
          {onGenerateChapters && (
            <Button
              variant="ghost"
              size="sm"
              onPress={() => onGenerateChapters(confirmedChapterIds)}
              isDisabled={confirmedChapterIds.length === 0}
            >
              生成选中章节正文
            </Button>
          )}
        </div>
      </div>

      {hint && <p className="text-xs text-muted">{hint}</p>}

      {outline.volumes.length === 0 && (
        <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-muted">
          还没有大纲内容，请先生成卷章大纲
        </p>
      )}

      {outline.volumes.map((volume, volumeIndex) => {
        const key = `volume-${volumeIndex}`;
        const open = expanded[key] ?? volumeIndex === 0;
        return (
          <div key={key} className="rounded-xl border border-border bg-surface p-4">
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="min-w-0 flex-1 rounded-lg border border-border px-3 py-1.5 text-sm font-medium"
                value={volume.title}
                disabled={readOnly}
                onChange={(event) => patchVolume(volumeIndex, { title: event.target.value })}
              />
              <span className="text-xs text-muted">{volume.chapters.length} 章</span>
              <Button
                variant="ghost"
                size="sm"
                onPress={() => setExpanded((prev) => ({ ...prev, [key]: !open }))}
              >
                {open ? "收起" : "展开"}
              </Button>
            </div>
            {open && (
              <div className="mt-3 space-y-3">
                <textarea
                  className="min-h-16 w-full rounded-lg border border-border px-3 py-2 text-sm"
                  value={volume.summary}
                  disabled={readOnly}
                  onChange={(event) => patchVolume(volumeIndex, { summary: event.target.value })}
                  placeholder="本卷概要"
                />
                {volume.chapters.map((chapter, chapterIndex) => (
                  <div
                    key={`${key}-chapter-${chapterIndex}`}
                    className="rounded-lg border border-border bg-surface-secondary p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        className="min-w-0 flex-1 rounded-lg border border-border px-3 py-1.5 text-sm"
                        value={chapter.title}
                        disabled={readOnly}
                        onChange={(event) =>
                          patchChapter(volumeIndex, chapterIndex, { title: event.target.value })
                        }
                      />
                      {chapter.chapter_id && (
                        <span className="text-xs text-muted">已创建 {chapter.chapter_id}</span>
                      )}
                      <button
                        type="button"
                        className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 disabled:opacity-40"
                        disabled={readOnly}
                        onClick={() => removeChapter(volumeIndex, chapterIndex)}
                      >
                        删除
                      </button>
                    </div>
                    <textarea
                      className="mt-2 min-h-16 w-full rounded-lg border border-border px-3 py-2 text-sm"
                      value={chapter.summary}
                      disabled={readOnly}
                      onChange={(event) =>
                        patchChapter(volumeIndex, chapterIndex, { summary: event.target.value })
                      }
                      placeholder="本章梗概"
                    />
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      <ListField
                        label="本章目标"
                        value={chapter.goals}
                        readOnly={readOnly}
                        onChange={(value) =>
                          patchChapter(volumeIndex, chapterIndex, { goals: value })
                        }
                      />
                      <ListField
                        label="主要冲突"
                        value={chapter.conflicts}
                        readOnly={readOnly}
                        onChange={(value) =>
                          patchChapter(volumeIndex, chapterIndex, { conflicts: value })
                        }
                      />
                      <ListField
                        label="伏笔"
                        value={chapter.foreshadowing}
                        readOnly={readOnly}
                        onChange={(value) =>
                          patchChapter(volumeIndex, chapterIndex, { foreshadowing: value })
                        }
                      />
                      <ListField
                        label="战力变化"
                        value={chapter.power_changes.map(
                          (change) =>
                            `${change.name || change.entity_id}：${change.from} → ${change.to}${
                              change.reason ? `（${change.reason}）` : ""
                            }`
                        )}
                        readOnly
                        onChange={() => undefined}
                      />
                    </div>
                  </div>
                ))}
                <Button variant="ghost" size="sm" onPress={() => addChapter(volumeIndex)} isDisabled={readOnly}>
                  新增章节
                </Button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ListField({
  label,
  value,
  onChange,
  readOnly,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  readOnly?: boolean;
}) {
  return (
    <label className="block text-xs">
      <span className="mb-1 block text-muted">{label}（每行一条）</span>
      <textarea
        className="min-h-14 w-full rounded-lg border border-border px-3 py-2"
        value={value.join("\n")}
        disabled={readOnly}
        onChange={(event) =>
          onChange(
            event.target.value
              .split(/[\n,，、;；]+/)
              .map((item) => item.trim())
              .filter(Boolean)
          )
        }
      />
    </label>
  );
}
