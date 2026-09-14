"""Manual real-browser smoke; uses an isolated local project and config."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.tools/browser'))
import tempfile
import threading
from playwright.sync_api import sync_playwright
from web_server import NovelWebServer


def main():
    with tempfile.TemporaryDirectory() as temp:
        server = NovelWebServer(('127.0.0.1', 0), workspace=temp, config_path=Path(temp)/'config.json')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge',headless=True)
                page = browser.new_page(viewport={'width':1350,'height':840},color_scheme='dark')
                errors=[]
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.wait_for_load_state('networkidle')
                page.get_by_role('button',name='Main Functions',exact=True).wait_for()
                page.get_by_role('button',name='LLM Model settings',exact=True).wait_for()
                page.screenshot(path=str(ROOT / '.tmp/web-original-desktop.png'),full_page=True)
                assert page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth'), 'Horizontal overflow'
                for name in ['Novel Architecture','Chapter Blueprint','Character State','Global Summary','章节编辑','设定卡库','Other Settings']:
                    page.get_by_role('button',name=name,exact=True).click()
                    assert page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth'), name
                page.get_by_role('button',name='Novel Architecture',exact=True).click()
                page.locator('#doc').fill('浏览器保存的架构')
                page.locator('#doc-save').click()
                page.locator('#status').filter(has_text='已保存').wait_for()
                assert (Path(temp)/'Novel_architecture.txt').read_text(encoding='utf-8') == '浏览器保存的架构'
                page.get_by_role('button',name='章节编辑',exact=True).click()
                page.locator('#ch-new').click()
                page.locator('#modal input').fill('1')
                page.locator('#modal-actions button').last.click(force=True)
                page.locator('#ch-text').wait_for(state='visible')
                page.locator('#ch-text:not([disabled])').wait_for()
                page.locator('#ch-text').fill('浏览器章节正文\n保留空白  ')
                page.locator('#ch-save').click()
                page.locator('#status').filter(has_text='章节已保存').wait_for()
                assert (Path(temp)/'chapters/chapter_1.txt').read_text(encoding='utf-8') == '浏览器章节正文\n保留空白  '
                assert not errors, errors
                page.set_viewport_size({'width':390,'height':844})
                page.get_by_role('button',name='Main Functions',exact=True).click()
                assert page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth'), 'Mobile overflow'
                page.screenshot(path=str(ROOT / '.tmp/web-original-mobile.png'),full_page=True)
                browser.close()
                print('Original Web navigation smoke passed; screenshots saved.')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    main()
