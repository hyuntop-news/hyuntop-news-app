from datetime import UTC, datetime
from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


OUT = Path("app_process_summary_editable_ko.pptx")
EMU = 914400


def emu(inch: float) -> int:
    return int(inch * EMU)


def xtext(text: str) -> str:
    return escape(str(text)).replace("\n", "&#10;")


def para(text: str, size: int = 1800, bold: bool = False, color: str = "F8FAFC") -> str:
    bold_xml = ' b="1"' if bold else ""
    blocks = []
    for line in str(text).split("\n"):
        blocks.append(
            f'<a:p><a:r><a:rPr lang="ko-KR" sz="{size}"{bold_xml}>'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
            '<a:latin typeface="Malgun Gothic"/><a:ea typeface="Malgun Gothic"/>'
            f"</a:rPr><a:t>{xtext(line)}</a:t></a:r>"
            f'<a:endParaRPr lang="ko-KR" sz="{size}"/></a:p>'
        )
    return "".join(blocks)


def box(
    sid: int,
    name: str,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    size: int = 1700,
    bold: bool = False,
    color: str = "F8FAFC",
    fill: str | None = None,
    line: str | None = "263248",
    round_rect: bool = True,
) -> str:
    fill_xml = f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
    line_xml = (
        f'<a:ln w="12700"><a:solidFill><a:srgbClr val="{line}"/></a:solidFill></a:ln>'
        if line
        else "<a:ln><a:noFill/></a:ln>"
    )
    geom = "roundRect" if round_rect else "rect"
    return f"""
    <p:sp>
      <p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
      <p:spPr>
        <a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(w)}" cy="{emu(h)}"/></a:xfrm>
        <a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>{fill_xml}{line_xml}
      </p:spPr>
      <p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="68580" rIns="91440" bIns="68580"/><a:lstStyle/>
        {para(text, size, bold, color)}
      </p:txBody>
    </p:sp>
    """


def slide(title: str, subtitle: str = "", shapes: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:bg><p:bgPr><a:solidFill><a:srgbClr val="070B16"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
      {box(2, "title", 0.55, 0.32, 12.0, 0.65, title, 3000, True, "FFFFFF", None, None, False)}
      {box(3, "subtitle", 0.62, 1.04, 11.6, 0.42, subtitle, 1450, False, "93C5FD", None, None, False) if subtitle else ""}
      {box(4, "divider", 0.6, 1.55, 12.1, 0.035, "", 1000, False, "FFFFFF", "263248", None, False)}
      {shapes}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>
"""


def bullets(start_id: int, items: list[str], x: float = 0.8, y: float = 2.0, w: float = 11.7, h: float = 3.9) -> str:
    sid = start_id
    row_h = h / max(len(items), 1)
    result = ""
    for item in items:
        result += box(sid, "bullet", x, y, w, row_h - 0.08, "• " + item, 1750, False, "E5E7EB", "111827", "263248", True)
        sid += 1
        y += row_h
    return result


def table(start_id: int, headers: list[str], rows: list[list[str]], x: float, y: float, widths: list[float], row_h: float = 0.62) -> str:
    sid = start_id
    result = ""
    cx = x
    for index, header in enumerate(headers):
        result += box(sid, "table header", cx, y, widths[index], row_h, header, 1450, True, "FFFFFF", "172554", "3B82F6", True)
        sid += 1
        cx += widths[index]
    y += row_h
    for row_index, row in enumerate(rows):
        cx = x
        fill = "111827" if row_index % 2 == 0 else "0F172A"
        for index, cell in enumerate(row):
            result += box(sid, "table cell", cx, y, widths[index], row_h, cell, 1320, False, "E5E7EB", fill, "263248", False)
            sid += 1
            cx += widths[index]
        y += row_h
    return result


slides = [
    slide(
        "아침 뉴스 자동화 앱 제작 과정",
        "뉴스 수집부터 이메일 발송, 콘텐츠 제작, 영상 생성, 배포 보안까지",
        box(
            5,
            "hero",
            0.75,
            2.05,
            11.8,
            2.6,
            "PowerShell 자동화에서 시작해\nStreamlit 대시보드형 웹앱으로 확장한 프로젝트",
            3000,
            True,
            "F8FAFC",
            "111827",
            "22D3EE",
            True,
        )
        + box(6, "date", 0.8, 5.15, 11.6, 0.6, "HYUNTOP NEWS · 수정 가능한 PPT", 1800, False, "CBD5E1", None, None, False),
    ),
    slide(
        "1. 앱의 목표",
        "코드를 몰라도 뉴스 콘텐츠 제작 흐름을 관리하는 도구",
        bullets(
            5,
            [
                "매일 아침 주요 뉴스를 자동으로 수집",
                "지정한 이메일로 뉴스 요약 발송",
                "뉴스 중 하나를 골라 블로그, 티스토리, 쓰레드, 유튜브 대본 생성",
                "PPTX와 영상 제작 패키지, MP4 영상까지 자동화",
                "웹 대시보드에서 설정과 결과물을 관리",
            ],
        ),
    ),
    slide(
        "2. 필요했던 프로그램",
        "앱 제작과 배포에 사용한 주요 도구",
        table(
            5,
            ["분류", "사용한 프로그램/서비스", "역할"],
            [
                ["개발", "Python / VS Code", "앱 기능 작성과 수정"],
                ["화면", "Streamlit", "웹 대시보드 제작"],
                ["메일", "Gmail 앱 비밀번호", "뉴스 자동 발송"],
                ["AI", "Google AI Studio / Gemini API", "블로그 글과 대본 생성"],
                ["배포", "GitHub / Streamlit Cloud", "웹앱 배포와 관리"],
                ["영상", "FFmpeg / 한글 폰트", "MP4 생성과 한글 표시"],
            ],
            0.65,
            1.95,
            [1.6, 3.9, 6.0],
            0.62,
        ),
    ),
    slide(
        "3. 대시보드 구성",
        "앱을 조작하는 중심 화면",
        table(
            5,
            ["대시보드 영역", "기능"],
            [
                ["왼쪽 사이드바", "대시보드, 설정, 실행 기록, 저장 콘텐츠 이동"],
                ["상단 상태 영역", "앱 제목, 현재 시간, LIVE 상태 표시"],
                ["Automation Overview", "키워드, 발송 시간, 수신 이메일, 콘텐츠 상태 표시"],
                ["Quick Actions", "지금 테스트 실행, 스케줄러 등록/갱신"],
                ["Saved Content", "블로그 글, 티스토리 글, 쓰레드, 대본, PPTX, 영상 제작 확인"],
            ],
            0.75,
            2.0,
            [3.1, 8.2],
            0.72,
        ),
    ),
    slide(
        "4. 콘텐츠 제작 기능",
        "뉴스 하나를 여러 플랫폼용 콘텐츠로 변환",
        table(
            5,
            ["생성 결과물", "설명"],
            [
                ["블로그 글", "뉴스를 바탕으로 긴 해설형 글 생성"],
                ["티스토리 글", "문단을 나누고 블로그 스타일로 재구성"],
                ["쓰레드 글", "짧은 SNS용 요약 글 생성"],
                ["유튜브 대본", "슬라이드 1~6 순서로 화면 문구와 내레이션 생성"],
                ["Vrew 대본", "영상 자막/음성 제작용 대본 생성"],
                ["PPTX", "유튜브 슬라이드 개수에 맞춘 편집 가능한 PPT 생성"],
            ],
            0.75,
            1.95,
            [2.3, 9.0],
            0.62,
        ),
    ),
    slide(
        "5. 배포와 보안",
        "공개 웹앱으로 쓰기 위한 안전장치",
        bullets(
            5,
            [
                "GitHub Desktop으로 수정한 파일을 Commit / Push",
                "Streamlit Cloud에서 웹앱으로 배포",
                "Streamlit Secrets로 Gmail, Gemini, 관리자 비밀번호 관리",
                "DASHBOARD_PASSWORD로 관리자 로그인 추가",
                "비밀번호를 모르면 대시보드와 실행 버튼을 사용할 수 없음",
            ],
        ),
    ),
]


content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
""" + "".join(
    f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
    for i in range(1, len(slides) + 1)
) + "</Types>"

root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

pres_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
""" + "".join(
    f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
    for i in range(1, len(slides) + 1)
) + "</Relationships>"

slide_ids = "".join(f'<p:sldId id="{255+i}" r:id="rId{i}"/>' for i in range(1, len(slides) + 1))
presentation = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:sldIdLst>{slide_ids}</p:sldIdLst>
<p:sldSz cx="12192000" cy="6858000" type="wide"/>
<p:notesSz cx="6858000" cy="9144000"/>
<p:defaultTextStyle><a:defPPr><a:defRPr lang="ko-KR"/></a:defPPr></p:defaultTextStyle>
</p:presentation>"""

now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>아침 뉴스 자동화 앱 제작 과정</dc:title>
<dc:creator>Codex</dc:creator>
<cp:lastModifiedBy>Codex</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""

app = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>Codex</Application><PresentationFormat>Widescreen</PresentationFormat><Slides>{len(slides)}</Slides>
</Properties>"""

with ZipFile(OUT, "w", ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", content_types)
    z.writestr("_rels/.rels", root_rels)
    z.writestr("docProps/core.xml", core)
    z.writestr("docProps/app.xml", app)
    z.writestr("ppt/presentation.xml", presentation)
    z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
    for index, slide_xml in enumerate(slides, 1):
        z.writestr(f"ppt/slides/slide{index}.xml", slide_xml)

print(OUT.resolve())
