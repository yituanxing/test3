"""Source contract for Excel acceptance and coherent one-player video evidence.

E2E Chromium still independently tests visual behavior and actual permissions.
"""
from pathlib import Path
from app import demo_excel_alerts

ROOT = Path(__file__).resolve().parents[1]

def test_real_page_has_one_real_player_linked_to_frame_seeking():
    page=(ROOT/"static"/"real.html").read_text(encoding="utf-8")
    assert page.count('id="videoClip"')==1
    assert page.count('id="videoClipPicker"')==1
    assert 'id="demoClipInput"' not in page
    assert 'id="demoClipPlayer"' not in page
    assert 'currentLocalVideoURL' not in page
    assert 'id="openRealVideo"' in page
    assert 'href="#videoEvidence"' in page
    assert 'seekVideoSourceFrame(frame)' in page
    assert 'player.currentTime=Math.min(offset,player.duration-0.04)' in page

def test_excel_missing_items_do_not_fake_supported_routes():
    page=(ROOT/"static"/"real.html").read_text(encoding="utf-8")
    assert "row.status==='missing'" in page
    assert "row.id===3||row.id===21" in page
    assert '关键能力缺失：不提供假入口' in page
    assert '合成归并只在隔离测试模式' in page
    assert demo_excel_alerts._READ_ONLY_ROUTES[3]=="/real#excelAcceptance"
    assert demo_excel_alerts._READ_ONLY_ROUTES[21]=="/real#excelAcceptance"
    for i in (6,7,8,9,10,11,12,22):
        assert demo_excel_alerts._READ_ONLY_ROUTES[i]=="/real#excelRuleShowcase"

def test_guide_does_not_send_acceptor_to_disabled_default_routes():
    text=(ROOT/"RECOGNITION_RESULTS_DELIVERY_20261015.md").read_text(
        encoding="utf-8")
    assert '默认 `ZD_LEGACY_MODE=0`' in text
    assert '/real#excelAcceptance' in text
    assert '/real#excelRuleShowcase' in text

def test_video_evidence_instructions_are_visible_not_in_hidden_legacy_container():
    script=(ROOT/"static"/"showcase_ui.js").read_text(encoding="utf-8")
    assert "if(modules.oldPlayer)video.append(modules.oldPlayer);" in script
    assert "if(videoPlayer)video.append(videoPlayer);" in script
    assert 'hiddenLegacy' not in script
    assert 'const primary=byId("videoClip")' in script
