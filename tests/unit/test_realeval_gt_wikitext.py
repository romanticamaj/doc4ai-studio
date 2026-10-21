"""Wikitext → GT converter (bench/realeval/gt_from_wikitext.py). Synthetic wikitext only; the real gt_raw is
converted once by the CLI (spec §4.4 / §6.1 preprocessing, before any engine sees the zh scans)."""
from __future__ import annotations

import json

import pytest

from bench.realeval import gt_from_wikitext as W
from bench.realeval.normalize import apply_errata, norm_gt


def r(s: str, **kw) -> str:
    return W.render(s, **kw)


def flat(s: str, **kw) -> str:
    return norm_gt(r(s, **kw))


# ---- templates

@pytest.mark.parametrize(("src", "want"), [
    ("{{專|五代|北宋}}之詞", "五代北宋之詞"),                 # every positional argument is printed text
    ("{{專|南山}}。", "南山"),
    ("{{書|漁家傲}}，", "漁家傲"),
    ("{{UnO|記|A03785-003}}載之事實", "記載之事實"),        # first = the character, second = its code
    ("{{SIC|southerwesterly|southwesterly}} over", "southerwesterlyover"),   # first = what is printed
    ("{{sic}}有大小", "有大小"),
    ("伍{{校|芳廷|廷芳}}。", "伍芳廷"),
    ("{{ts|vtp|pr2|al|w40}}", ""),                         # table style codes, no text
    ("前{{gap|1em}}後{{Gap}}末", "前後末"),
    ("甲{{redact|width=3em}}乙", "甲乙"),
    ("{{*|錄入者註：推斷錄入。}}正文", "正文"),               # transcriber's note, not printed
    ("影{{?}}印", "影印"),
    ("{{c|{{sp|AN}}}} {{center|-2-}} {{right|546}}", "an2546"),
    ("{{big|院長}}{{larger|著}}{{x-larger|話}}{{xxl|KER}}{{xxxl|ACC}}", "院長著話kerAcc".lower()),
    ("{{lsp|0.4em|人間詞|話}}", "人間詞話"),                  # first argument is the spacing
    ("{{多行合一|台灣省縣市公職人員|選舉罷免監察委員會}}", "台灣省縣市公職人員選舉罷免監察委員會"),
    ("{{3|甲|乙|丙}}", "甲乙丙"),
    ("{{++|臺灣高雄地方法院（函）}}", "臺灣高雄地方法院函"),
    ("{{di|T}}HE {{hi|Shewing}} {{sm|Matth.}} {{l|x}}", "theshewingmatthx"),
    ("Mini{{ls}}ter {{ls+t}}imes a{{ss}}urance", "ministerstimesassurance"),
    ("{{Indent pars|\n諸位朋友\n}}", "諸位朋友"),
    ("{{Pre2|台灣\n高雄}}", "台灣高雄"),
    ("{{nop}}{{nopt}}{{dhr}}{{rule}}{{bar|2}}文", "文"),
    ("{{RunningHeader||1|精股}}", ""),
    ("{{continues|crown}}", ""),
])
def test_templates(src, want):
    assert flat(src) == want


def test_ppoem_is_expanded():
    src = "{{ppoem|class=x|\n民國十五年二月出版\n<>實價貳角\n>著作者\n}}"
    assert flat(src) == "民國十五年二月出版實價貳角著作者"


def test_unknown_template_fails_loudly():
    with pytest.raises(W.UnknownTemplate, match="nosuch"):
        r("x {{nosuch|y}} z")


def test_named_arguments_are_not_text():
    assert flat("{{c|style=font-size:2em|標題}}") == "標題"


# ---- tags, links, tables, formatting

def test_refs_and_reference_lists_are_dropped():
    assert flat('第二黎<ref>疑为“軍”字之誤</ref>駐紮<ref name="a"/><references/>') == "第二黎駐紮"


def test_links_show_their_display_text():
    assert flat("[[w:陳錫卿|陳錫卿]]、[[臺灣高等法院|臺高院]]、[[w:沈秉堃]]、[[戰時條例]]") == "陳錫卿臺高院沈秉堃戰時條例"


def test_html_and_formatting():
    src = "<div style=\"x\">一</div><u>二</u><small>三</small><pre>四</pre>'''五'''''六''<br />七<section begin=\"a\" />"
    assert flat(src) == "一二三四五六七"
    assert "\n" in r("甲<br/>乙")


def test_table_cells_without_attributes():
    src = '{| class="wikitable"\n|colspan="2"|受文者||高雄縣政府\n|-\n! 標題 !! 欄\n|-\n|{{ts|vtp|al}}|CALIFORNIA\n|}'
    assert flat(src) == "受文者高雄縣政府標題欄california"


def test_magic_words_and_pagequality_are_dropped():
    assert flat('<pagequality level="3" user="x" />__NOTOC__正文') == "正文"


# ---- the page structure

def test_noinclude_furniture_is_dropped_but_spanning_wrappers_work():
    raw = ('<noinclude><pagequality level="3" user="x" />{{RunningHeader||1|精股}}{{Pre2|</noinclude>本文\n第二行}}'
           "<noinclude>{{continues|crown}}</noinclude>")
    assert flat(raw) == "本文第二行"


def test_noinclude_table_header_is_dropped():
    raw = ("<noinclude>{{right|546}}\n{|\n|-\n|{{ts|al}}|HEADER</noinclude>\n|-\n|{{ts|al}}|BODY\n"
           "<noinclude>{{nopt}}\n|}</noinclude>")
    assert flat(raw) == "body"


def test_page_transclusion_resolves_through_the_resolver():
    seen = []

    def resolve(kind, title):
        seen.append((kind, title))
        return "<noinclude>{{right|21}}</noinclude>一　本報{{{1|暫定}}}則例"
    assert flat("{{Page:ROC1912-01-29臨時政府公報01.pdf/21|{{{1|曰令示}}}}}", resolve=resolve) == "一本報曰令示則例"
    assert flat("{{Page:X.pdf/2}}", resolve=resolve) == "一本報暫定則例"          # no argument → the default
    assert seen[0] == ("page", "Page:ROC1912-01-29臨時政府公報01.pdf/21")


def test_unresolvable_transclusion_fails_loudly():
    with pytest.raises(W.UnknownTemplate, match="Page:"):
        r("{{Page:X.pdf/1}}", resolve=lambda kind, title: None)


TPL = ("<div>\n第{{{課時}}}課\n（一）{{{1}}}認識\n{|\n|-\n| {{{字符一}}} || {{Gap|1.5em}} || {{{字符二}}}\n|-\n"
       "{{#if:{{{字符四|}}}| {{!}} {{{字符三}}} {{!}}{{!}} {{{字符四}}} | ! colspan=\"3\" {{!}} {{{字符三}}} }}\n|}\n"
       "——{{{頁碼}}}——\n</div>")


def test_user_template_is_expanded_from_its_fetched_definition():
    def resolve(kind, title):
        return TPL if (kind, title) == ("template", "User:樹上喜鵲/模板:sandbox") else None
    page = "{{\n\nUser:樹上喜鵲/模板:sandbox|再|課時=二\n|字符一 = ㄇ\n|字符二 = ㄈ\n|字符三 = ㄟ\n|字符四 = ㄢ\n|頁碼=8}}"
    assert flat(page, resolve=resolve) == "第二課一再認識ㄇㄈㄟㄢ8"
    three = "{{User:樹上喜鵲/模板:sandbox|再|課時=三|字符一=ㄅ|字符二=ㄆ|字符三=ㄚ|頁碼=9}}"
    assert flat(three, resolve=resolve) == "第三課一再認識ㄅㄆㄚ9"          # #if empty → the colspan branch


def test_check_text_flags_near_empty_and_markup():
    assert W.check_text("正文" * 20) == []
    assert W.check_text("短") == ["near_empty"]
    assert "markup" in W.check_text("正文" * 20 + "{{專|x") and "markup" in W.check_text("正文" * 20 + "<ref>")
    assert "markup" in W.check_text("正文" * 20 + "[[w:x")


# ---- errata after the rebuild

E = [{"doc": "d", "page": 1, "wrong": "判決書所載", "right": "判決書所記載", "source": "UnO dropped"},
     {"doc": "d", "page": 1, "wrong": "當旣出帥", "right": "當卽出帥", "source": "print shows 卽"},
     {"doc": "d", "page": 2, "wrong": "{{Page:X.pdf/21|{}}}", "right": "暫定則例全文", "source": "transcribed"},
     {"doc": "d", "page": 3, "wrong": "甲乙", "right": "甲丙", "source": "?"}]


def test_rebase_errata_classifies_every_entry():
    new = {1: "判決書所記載之事實，當旣出帥", 2: "重建後的頁面", 3: "沒有對應"}
    out = W.rebase_errata(E, {"d": new}, transcribed={("d", 2)})
    assert [x["status"] for x in out] == ["superseded", "kept", "replace_page", "unresolved"]
    kept = [x["erratum"] for x in out if x["status"] in ("kept", "replace_page")]
    assert kept[1]["replace_page"] is True and kept[1]["right"] == "暫定則例全文"


def test_apply_errata_replaces_a_whole_page():
    e = [{"doc": "d", "page": 2, "wrong": "", "right": "全頁轉錄", "replace_page": True, "source": "scan"}]
    assert apply_errata({1: "a", 2: "重建"}, e, "d") == {1: "a", 2: "全頁轉錄"}


def test_rebuild_writes_pages_and_a_report(tmp_path):
    d = tmp_path / "zh-x"
    (d / "gt_raw").mkdir(parents=True)
    (d / "gt").mkdir()
    (d / "gt" / "page-001.txt").write_text("舊", encoding="utf-8")
    (d / "gt_raw" / "page-001.wikitext").write_text("<noinclude>{{right|1}}</noinclude>{{專|五代|北宋}}之詞", "utf-8")
    rep = W.rebuild_doc(d, [1])
    assert (d / "gt" / "page-001.txt").read_text(encoding="utf-8").strip() == "五代北宋之詞"
    assert (d / "gt_v1" / "page-001.txt").read_text(encoding="utf-8") == "舊"     # the old GT is kept once
    assert rep == [{"page": 1, "before": 1, "after": 6, "problems": ["near_empty"]}]       # 6 < 20 characters
    json.dumps(rep)


def test_a_table_opened_in_the_header_on_the_noinclude_line():
    """en-aashto pattern: `{|</noinclude>{{nopt}}` on one line — the table start must not eat the closing tag."""
    raw = ('<noinclude><pagequality level="4" user="x" />{{right|548}}\n{|</noinclude>{{nopt}}\n|-\n'
           "|{{ts|vtp|al}}|KANSAS\n|-\n|{{ts|al}}|\n:Relocation of U.S. Route 36\n:APPROVED\n"
           "|{{ts|vtp|al}}|Beginning at the line.<noinclude>{{nopt}}\n|}\n{{center|-3-}}</noinclude>")
    assert flat(raw) == "kansasrelocationofusroute36approvedbeginningattheline"


def test_rebase_realigns_a_scan_reading_against_the_rebuilt_text():
    """ncl p18 pattern: the rebuild prints 點絳唇, the verifier read 點絳脣 from the scan → wrong := the rebuilt
    window (unique, edit distance <= 2); the scan reading stays the GT."""
    e = [{"doc": "d", "page": 1, "wrong": "人知和靖，舜兪，永叔", "right": "人知和靖點絳脣，舜兪蘇幕遮，永叔少年", "source": "scan"}]
    out = W.rebase_errata(e, {"d": {1: "前文人知和靖點絳唇，舜兪蘇幕遮，永叔少年\n後文"}}, transcribed=set())
    assert out[0]["status"] == "realigned"
    fixed = out[0]["erratum"]
    assert fixed["wrong"] == "人知和靖點絳唇，舜兪蘇幕遮，永叔少年" and fixed["right"] == e[0]["right"]
    assert apply_errata({1: "前文人知和靖點絳唇，舜兪蘇幕遮，永叔少年\n後文"}, [fixed], "d")[1].count("脣") == 1
    far = W.rebase_errata(e, {"d": {1: "完全不同的一頁"}}, transcribed=set())
    assert far[0]["status"] == "unresolved"
