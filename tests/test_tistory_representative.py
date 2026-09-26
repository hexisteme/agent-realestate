"""Synthetic Tistory publish dialog, not evidence of a live platform upload."""
import hashlib
import base64
from io import BytesIO

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from blog.tistory_media import DraftMedia, MediaContractError
from blog.tistory_publish_pw import attach_representative_card, pasted_anchors_present


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def media():
    buffer = BytesIO()
    Image.new("RGB", (1200, 630), "navy").save(buffer, format="PNG")
    content = buffer.getvalue()
    return DraftMedia("/not/reopened/card.png", hashlib.sha256(content).hexdigest(), "card", content)


def dialog(page, *, count=1, preview="img"):
    page.set_content('''<input id="post-title-inp" value="original title">
      <input id="tagText" value="original tags">
      <iframe id="editor-tistory_ifr" srcdoc="<body>original body</body>"></iframe>
      <button id="publish-btn">공개발행</button>'''
      + '<div class="inner_box"><input type="file" accept="image/*" class="inp_g">'
        '<i class="mce-ico ico_attach"></i><span class="txt_thumb">대표이미지 추가</span></div>' * count)
    page.evaluate('''(preview) => {
      for(const input of document.querySelectorAll('input[type=file]')) {
        input.addEventListener('change', () => {
          const file=input.files[0]; window.selected={name:file.name,type:file.type,size:file.size};
          if(preview==='none') return;
          const url=URL.createObjectURL(file);
          if(preview==='img') {const img=new Image();img.src=url;input.parentElement.append(img);}
          else {input.parentElement.style.backgroundImage=`url("${url}")`;}
        });
      }
    }''', preview)


@pytest.mark.parametrize("preview", ["img", "background"])
def test_observed_native_input_binds_verified_bytes_without_editor_change(browser, media, preview):
    page = browser.new_page()
    try:
        page.set_default_timeout(250)
        dialog(page, preview=preview)
        original = page.frame_locator("iframe").locator("body").inner_html()
        attach_representative_card(page, media)
        assert page.evaluate("window.selected") == {"name": "card.png", "type": "image/png", "size": len(media.image_bytes)}
        uploaded = page.evaluate("async()=>Array.from(new Uint8Array(await document.querySelector('input[type=file]').files[0].arrayBuffer()))")
        assert bytes(uploaded) == media.image_bytes
        assert page.frame_locator("iframe").locator("body").inner_html() == original
        assert page.locator("#post-title-inp").input_value() == "original title"
        assert page.locator("#tagText").input_value() == "original tags"
    finally:
        page.close()


@pytest.mark.parametrize("text,expected", [("first middle last", True), ("first", False), ("", False)])
def test_every_anchor_required_in_actual_iframe(browser, text, expected):
    page = browser.new_page()
    try:
        dialog(page)
        page.frame_locator("iframe").locator("body").evaluate("(body,text)=>body.textContent=text", text)
        assert pasted_anchors_present(page, ["first", "middle", "last"]) is expected
        assert not pasted_anchors_present(page, [])
    finally:
        page.close()


@pytest.mark.parametrize("count,preview", [(0, "img"), (2, "img"), (1, "none")])
def test_missing_ambiguous_or_no_preview_fails_closed(browser, media, count, preview):
    page = browser.new_page()
    try:
        dialog(page, count=count, preview=preview)
        page.set_default_timeout(250)
        with pytest.raises(MediaContractError):
            attach_representative_card(page, media)
    finally:
        page.close()


@pytest.mark.parametrize("transition,expected", [
    ("unchanged", False), ("replace_empty", False), ("replace_loaded", True),
    ("update_src", True), ("background_unchanged", False), ("background_updated", True),
    ("delayed_detached", False), ("replace_without_label", True),
])
def test_requires_fresh_preview_in_current_native_container(browser, media, transition, expected):
    page = browser.new_page()
    try:
        dialog(page, preview="none")
        page.evaluate('''async ({png,transition}) => {
            const box=document.querySelector('.inner_box');
            const old=new Image(); old.src='data:image/png;base64,'+png; await old.decode();
            if(transition.startsWith('background')) box.style.backgroundImage=`url("${old.src}")`;
            else box.append(old);
            const input=box.querySelector('input');
            input.addEventListener('change', () => {
                const url=URL.createObjectURL(input.files[0]);
                if(transition==='update_src') old.src=url;
                if(transition==='background_updated') box.style.backgroundImage=`url("${url}")`;
                if(transition==='delayed_detached') setTimeout(() => {
                    const fresh=box.cloneNode(true); fresh.querySelectorAll('img').forEach(i=>i.remove());
                    box.replaceWith(fresh); old.src=url;
                }, 100);
                if(transition.startsWith('replace_')) {
                    const fresh=box.cloneNode(true); fresh.querySelectorAll('img').forEach(i=>i.remove());
                    box.replaceWith(fresh);
                    if(transition==='replace_loaded' || transition==='replace_without_label') {
                        const img=new Image();img.src=url;fresh.append(img);
                        if(transition==='replace_without_label') fresh.querySelector('.txt_thumb').remove();
                    }
                    // Detached old container retains its loaded image: it must never count.
                }
            });
        }''', {"png": base64.b64encode(media.image_bytes).decode(), "transition": transition})
        if expected:
            attach_representative_card(page, media)
        else:
            with pytest.raises(MediaContractError):
                attach_representative_card(page, media)
    finally:
        page.close()
