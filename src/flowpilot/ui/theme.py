"""Visual layer for the reviewer console: CSS and inline SVG artwork.

Everything is local. Streamlit's `st.html` sanitizer strips inline `<svg>`, so artwork is
embedded as `data:` URIs inside CSS; the only URL in any SVG is its XML namespace, which is never
fetched. Fonts come from the operating system (no web fonts).
"""

from __future__ import annotations

from urllib.parse import quote

SVG_NS = "http://www.w3.org/2000/svg"

NIGHT = "#0A1430"
DUSK = "#13265C"
SIGNAL = "#1F3FBF"
SKY = "#7BA2FF"
INK = "#0A1433"
AMBER = "#F2A65A"


def svg_url(body: str, view_box: str) -> str:
    """CSS `url()` for an SVG fragment. Attributes inside `body` must use single quotes."""
    svg = f"<svg xmlns='{SVG_NS}' viewBox='{view_box}'>{body}</svg>"
    return f'url("data:image/svg+xml,{quote(svg, safe=" =:/,.()-")}")'


def _icon(paths: str, stroke: str = "#FFFFFF") -> str:
    return svg_url(
        f"<g fill='none' stroke='{stroke}' stroke-width='1.8' stroke-linecap='round' "
        f"stroke-linejoin='round'>{paths}</g>",
        "0 0 24 24",
    )


LOGO = svg_url(
    "<defs><linearGradient id='g' x1='0' y1='1' x2='1' y2='0'>"
    f"<stop offset='0' stop-color='#4F7BFF'/><stop offset='1' stop-color='{SKY}'/>"
    "</linearGradient></defs>"
    "<path d='M9 33c6 0 7-8 12-12s9 1 13-6' fill='none' stroke='url(#g)' stroke-width='4.2' "
    "stroke-linecap='round'/>"
    "<circle cx='9' cy='33' r='4.6' fill='#4F7BFF'/>"
    f"<circle cx='35.5' cy='12.5' r='5.6' fill='{SKY}'/>",
    "0 0 44 44",
)

ICON_CAPTURE = _icon(
    "<path d='M4 13h4l1.5 2.5h5L16 13h4'/>"
    "<path d='M6 5h12l3 8v5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18v-5z'/>"
)
ICON_QUALIFY = _icon(
    "<path d='M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V7z'/>"
    "<path d='M14 3v4h4'/><path d='m9.5 14 2 2 3.5-4'/>"
)
ICON_REVIEW = _icon("<circle cx='12' cy='8' r='3.6'/><path d='M5 20a7 7 0 0 1 14 0'/>")
ICON_LOCK = _icon(
    "<rect x='5' y='11' width='14' height='9.5' rx='2'/>"
    "<path d='M8.5 11V7.5a3.5 3.5 0 0 1 7 0V11'/>"
    "<circle cx='12' cy='15.6' r='1'/>",
    stroke="#7A86A1",
)
ICON_CHECK = _icon("<path d='m6 12.5 4 4 8-9'/>")

# Workflow connectors in the same 600x240 space as the node positions in CSS.
_FLOW_PATHS = (
    "M172 52 C 196 52, 192 110, 214 110",
    "M388 110 C 410 110, 406 178, 428 178",
)
_FLOW_DOTS = ((172, 52), (214, 110), (388, 110), (428, 178))


def _flow(animated: bool) -> str:
    parts = []
    if animated:
        parts.append(
            "<style>.p{stroke-dasharray:16 220;animation:run 4.2s linear infinite}"
            ".d{animation-delay:2.1s}"
            "@keyframes run{from{stroke-dashoffset:236}to{stroke-dashoffset:0}}</style>"
        )
    for d in _FLOW_PATHS:
        parts.append(
            f"<path d='{d}' fill='none' stroke='{SKY}' stroke-opacity='.45' stroke-width='1.6'/>"
        )
    if animated:
        for i, d in enumerate(_FLOW_PATHS):
            cls = "p d" if i else "p"
            parts.append(
                f"<path class='{cls}' d='{d}' fill='none' stroke='#DCE6FF' stroke-width='2.4' "
                "stroke-linecap='round'/>"
            )
    for x, y in _FLOW_DOTS:
        parts.append(f"<circle cx='{x}' cy='{y}' r='7' fill='{SKY}' fill-opacity='.18'/>")
        parts.append(f"<circle cx='{x}' cy='{y}' r='3.2' fill='#B9CCFF'/>")
    return svg_url("".join(parts), "0 0 600 240")


FLOW_ANIMATED = _flow(animated=True)
FLOW_STATIC = _flow(animated=False)


def _skyline() -> str:
    """A dusk skyline: far towers, near blocks, and a deterministic pattern of lit windows."""
    far = [
        (300, 26, 150),
        (330, 18, 118),
        (352, 30, 196),
        (388, 22, 132),
        (414, 34, 228),
        (452, 20, 160),
        (476, 28, 206),
        (508, 24, 140),
        (536, 32, 182),
        (572, 28, 124),
        (40, 30, 96),
        (78, 22, 120),
        (104, 28, 88),
        (150, 24, 110),
        (200, 30, 132),
        (238, 22, 104),
        (264, 30, 140),
    ]
    near = [
        (0, 46, 70),
        (50, 38, 92),
        (92, 52, 62),
        (150, 40, 84),
        (194, 56, 68),
        (256, 44, 100),
        (306, 60, 76),
        (372, 42, 112),
        (420, 58, 86),
        (484, 40, 104),
        (530, 70, 72),
    ]
    out = [
        # Depth: horizon glow, a hazy distant layer, lit far towers, haze, dark near blocks.
        "<defs>"
        "<radialGradient id='glow' cx='.68' cy='1' r='.62'>"
        f"<stop offset='0' stop-color='{AMBER}' stop-opacity='.42'/>"
        f"<stop offset='.55' stop-color='{AMBER}' stop-opacity='.08'/>"
        f"<stop offset='1' stop-color='{AMBER}' stop-opacity='0'/></radialGradient>"
        "<linearGradient id='far' x1='0' y1='0' x2='0' y2='1'>"
        "<stop offset='0' stop-color='#22397A'/>"
        f"<stop offset='1' stop-color='{DUSK}'/></linearGradient>"
        "<linearGradient id='near' x1='0' y1='0' x2='0' y2='1'>"
        "<stop offset='0' stop-color='#0D1A42'/><stop offset='1' stop-color='#060D24'/>"
        "</linearGradient>"
        "<linearGradient id='haze' x1='0' y1='0' x2='0' y2='1'>"
        f"<stop offset='0' stop-color='{SKY}' stop-opacity='0'/>"
        f"<stop offset='.6' stop-color='{SKY}' stop-opacity='.10'/>"
        f"<stop offset='1' stop-color='{SKY}' stop-opacity='0'/></linearGradient>"
        "</defs>"
        "<rect x='0' y='40' width='600' height='220' fill='url(#glow)'/>"
    ]
    out += [
        f"<rect x='{x + 14}' y='{260 - h * 0.78:.0f}' width='{w}' height='{h}' fill='#1A2E68' "
        "fill-opacity='.45'/>"
        for x, w, h in far
    ]
    out += [
        f"<rect x='{x}' y='{260 - h}' width='{w}' height='{h}' fill='url(#far)'/>"
        for x, w, h in far
    ]
    out += [
        f"<rect x='{x}' y='{260 - h}' width='{w}' height='1.2' fill='#9DB6FF' fill-opacity='.35'/>"
        for x, w, h in far
        if h >= 180
    ]
    for i, (x, w, h) in enumerate(far):
        for row in range(4, h // 9):
            for col in range(1, w // 7):
                n = i * 31 + row * 7 + col * 13
                if n % 11 == 0:
                    color, alpha = (AMBER, ".75") if n % 3 else ("#A9C1FF", ".55")
                    out.append(
                        f"<rect x='{x + col * 7 - 1}' y='{260 - h + row * 9}' width='2.4' "
                        f"height='3.2' fill='{color}' fill-opacity='{alpha}'/>"
                    )
    out.append("<rect x='0' y='130' width='600' height='110' fill='url(#haze)'/>")
    out += [
        f"<rect x='{x}' y='{260 - h}' width='{w}' height='{h}' fill='url(#near)'/>"
        for x, w, h in near
    ]
    return svg_url("".join(out), "0 0 600 260")


SKYLINE = _skyline()

FONT_STACK = (
    '"Avenir Next", "Avenir", "Segoe UI Variable Display", "Segoe UI", -apple-system, '
    'BlinkMacSystemFont, Roboto, "Helvetica Neue", Arial, sans-serif'
)

_CSS = """
:root {
  --fp-night: __NIGHT__;
  --fp-dusk: __DUSK__;
  --fp-signal: __SIGNAL__;
  --fp-signal-deep: #1A34A3;
  --fp-sky: __SKY__;
  --fp-ink: __INK__;
  --fp-muted: #5E6A85;
  --fp-quiet: #8A94AB;
  --fp-line: #E3E8F1;
  --fp-field: #F5F7FB;
  --fp-field-line: #D8DFEB;
}
.stApp *:not([data-testid="stIconMaterial"]) { font-family: __FONT__; }
.stApp {
  background:
    radial-gradient(900px 620px at 12% 8%, rgba(60, 96, 200, .30), transparent 62%),
    radial-gradient(760px 520px at 88% 96%, rgba(242, 166, 90, .14), transparent 60%),
    linear-gradient(160deg, #0D1A3E 0%, #070E24 58%, #0A1430 100%);
}
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"], [data-testid="InputInstructions"] { display: none; }
.block-container { max-width: 1240px; padding: 4vh 24px 3vh; }

/* Shell: one rounded surface split into story and sign-in. */
.st-key-fp_shell {
  background: #FFFFFF;
  border-radius: 28px;
  overflow: hidden;
  gap: 0;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .08), 0 40px 120px -32px rgba(0, 0, 0, .7);
}
.st-key-fp_shell [data-testid="stHorizontalBlock"] { gap: 0; align-items: stretch; }
.st-key-fp_shell [data-testid="stColumn"]:first-child {
  background:
    linear-gradient(to top, rgba(5, 10, 28, .96) 0%, rgba(5, 10, 28, .72) 30%,
      rgba(5, 10, 28, 0) 56%),
    __SKYLINE__ center bottom / 100% auto no-repeat,
    radial-gradient(520px 220px at 72% 82%, rgba(242, 166, 90, .30), transparent 70%),
    linear-gradient(180deg, #0B1740 0%, #10245A 52%, #1B2F66 74%, var(--fp-night) 100%);
}

/* Story panel */
.fp-story {
  display: flex; flex-direction: column; min-height: 700px; box-sizing: border-box;
  padding: 52px clamp(28px, 4.6vw, 64px) 56px; color: #FFFFFF; overflow: hidden;
}
.fp-brand { display: flex; align-items: center; gap: 14px; }
.fp-mark { width: 46px; height: 46px; flex: none; background: __LOGO__ center / contain no-repeat; }
.fp-wordmark { font-size: 1.85rem; font-weight: 600; letter-spacing: -.02em; line-height: 1; }
.fp-wordmark span { color: var(--fp-sky); }
.fp-tagline {
  display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 22px 0 0; padding: 0;
  list-style: none; font-size: .72rem; font-weight: 600; letter-spacing: .2em;
  text-transform: uppercase; color: rgba(214, 224, 250, .62);
}
.fp-tagline li + li::before { content: "•"; margin-right: 14px; color: rgba(123, 162, 255, .7); }

.fp-flow {
  position: relative; container-type: inline-size; aspect-ratio: 600 / 240;
  margin: 48px 0 0; background: __FLOW__ center / 100% 100% no-repeat;
}
.fp-node {
  position: absolute; width: 28.6%; transform: translateY(-50%); box-sizing: border-box;
  display: flex; align-items: center; gap: 2.4cqw; padding: 1.9cqw 2.2cqw;
  border-radius: 2.6cqw; border: 1px solid rgba(140, 170, 255, .34);
  background: linear-gradient(180deg, rgba(34, 56, 120, .62), rgba(16, 30, 72, .72));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .08), 0 18px 40px -18px rgba(0, 0, 0, .8);
  backdrop-filter: blur(6px);
}
.fp-node:nth-child(1) { left: 0; top: 21.7%; }
.fp-node:nth-child(2) { left: 35.7%; top: 45.8%; }
.fp-node:nth-child(3) { left: 71.4%; top: 74.2%; }
.fp-node i {
  flex: none; width: 7.4cqw; height: 7.4cqw; border-radius: 50%;
  background: var(--fp-icon) center / 56% no-repeat,
    radial-gradient(circle at 30% 25%, #5B83FF, #2443B8 70%);
  box-shadow: 0 0 0 1px rgba(160, 188, 255, .35), 0 0 22px rgba(79, 123, 255, .45);
}
.fp-node:nth-child(1) i { --fp-icon: __ICON_CAPTURE__; }
.fp-node:nth-child(2) i { --fp-icon: __ICON_QUALIFY__; }
.fp-node:nth-child(3) i { --fp-icon: __ICON_REVIEW__; }
.fp-node b { font-size: 2.55cqw; font-weight: 500; line-height: 1.25; color: #F1F5FF; }

.fp-headline {
  margin: auto 0 0; padding-top: 40px; font-size: clamp(2rem, 3.1vw, 2.95rem); font-weight: 700;
  line-height: 1.08; letter-spacing: -.028em; color: #FFFFFF; max-width: 19ch;
  text-wrap: balance;
}
.fp-headline span { color: var(--fp-sky); }
.fp-copy {
  margin: 18px 0 0; max-width: 40ch; text-wrap: pretty; font-size: 1.06rem; line-height: 1.55;
  color: rgba(222, 230, 252, .80);
}

/* Sign-in panel */
.st-key-fp_signin {
  min-height: 700px; box-sizing: border-box; gap: 0;
  padding: 56px clamp(28px, 6vw, 96px) 34px;
}
.st-key-fp_signin [data-testid="stElementContainer"]:has(.fp-welcome),
.st-key-fp_signin [data-testid="stElementContainer"]:has(.fp-assure) { margin-top: auto; }
.st-key-fp_signin > * { max-width: 470px; width: 100%; }
.fp-welcome h1 {
  margin: 0; padding: 0; font-size: clamp(2.4rem, 3.7vw, 3.45rem); font-weight: 700;
  letter-spacing: -.035em; line-height: 1.04; color: var(--fp-ink);
}
.fp-welcome p { margin: 12px 0 40px; font-size: 1.2rem; line-height: 1.45; color: var(--fp-muted); }
.st-key-fp_signin [data-testid="stForm"] { padding: 0; border: 0; }
.st-key-fp_signin [data-testid="stWidgetLabel"] p {
  font-size: .98rem; font-weight: 600; color: var(--fp-ink); margin-bottom: 4px;
}
.st-key-fp_signin [data-testid="stTextInputRootElement"] {
  height: 58px; border-radius: 12px; border: 1px solid var(--fp-field-line);
  background: var(--fp-field); transition: border-color .15s ease, box-shadow .15s ease;
}
.st-key-fp_signin [data-testid="stTextInputRootElement"]:focus-within {
  border-color: var(--fp-signal); box-shadow: 0 0 0 4px rgba(31, 63, 191, .14);
}
.st-key-fp_signin [data-testid="stTextInputRootElement"] input {
  font-size: 1.08rem; padding-left: 18px; color: var(--fp-ink); background: transparent;
}
.st-key-fp_signin [data-testid="stTextInputRootElement"] input::placeholder { color: #9AA4BA; }
.st-key-fp_signin [data-testid="stTextInputRootElement"] button {
  color: #6B7896; background: transparent; margin-right: 8px;
}
.st-key-fp_signin [data-testid="stTextInputRootElement"] button:focus-visible {
  outline: 2px solid var(--fp-signal); outline-offset: 2px; border-radius: 8px;
}
.st-key-fp_signin [data-testid="stFormSubmitButton"] { margin-top: 18px; }
.st-key-fp_signin [data-testid="stFormSubmitButton"] button {
  width: 100%; height: 60px; border-radius: 12px; border: 1px solid var(--fp-signal-deep);
  background: linear-gradient(180deg, #2546CC, var(--fp-signal-deep));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .18), 0 14px 30px -14px rgba(31, 63, 191, .75);
  transition: filter .15s ease, box-shadow .15s ease, transform .06s ease;
}
.st-key-fp_signin [data-testid="stFormSubmitButton"] button p,
.st-key-fp_signin [data-testid="stFormSubmitButton"] button span {
  font-size: 1.12rem; font-weight: 600; color: #FFFFFF;
}
.st-key-fp_signin [data-testid="stFormSubmitButton"] button:hover {
  filter: brightness(1.08);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .2), 0 18px 34px -14px rgba(31, 63, 191, .9);
}
.st-key-fp_signin [data-testid="stFormSubmitButton"] button:active { transform: translateY(1px); }
.st-key-fp_signin [data-testid="stFormSubmitButton"] button:focus-visible {
  outline: none; box-shadow: 0 0 0 4px rgba(31, 63, 191, .3);
}
.st-key-fp_signin [data-testid="stFormSubmitButton"] button:disabled {
  filter: saturate(.55) brightness(1.12); box-shadow: none; cursor: progress; transform: none;
}
.st-key-fp_signin [data-testid="stAlert"] { margin-top: 18px; }
.st-key-fp_signin [data-testid="stAlert"] > div { border-radius: 12px; }
.fp-note {
  display: flex; gap: 12px; align-items: flex-start; margin-top: 26px;
  font-size: .95rem; line-height: 1.5; color: var(--fp-quiet);
}
.fp-note::before {
  content: ""; flex: none; width: 22px; height: 22px; margin-top: 1px;
  background: __ICON_LOCK__ center / contain no-repeat;
}


.fp-assure {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  justify-content: center;
  align-items: center;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
  margin: 40px 0 0;
  padding: 20px 0 0;
  list-style: none;
  border-top: 1px solid var(--fp-line);
  font-size: .72rem;
  line-height: 1.4;
  color: var(--fp-quiet);
  text-align: center;
  white-space: nowrap;
}

.fp-assure-separator {
  flex-shrink: 0;
}


.fp-credit {
  margin-top: 12px;
}


.fp-credit > p {
  display: block;
  width: 100%;
  margin: 0 !important;
  text-align: center !important;
}



.fp-footer {
  width: 100%;
  display: block;
  text-align: center;
}

.fp-credit {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  box-sizing: border-box;
  gap: 5px;
  margin: 16px auto 0;
  text-align: center;
}

.fp-credit > p {
  display: block;
  width: 100%;
  margin: 0 !important;
  text-align: center !important;
}

.fp-credit .fp-credit-intro, .fp-credit .fp-credit-role {
  margin: 0; font-size: .74rem; line-height: 1.35; letter-spacing: .01em;
  color: var(--fp-quiet); text-wrap: balance;
}
.fp-credit .fp-credit-name {
  margin: 0; font-size: .86rem; line-height: 1.3; font-weight: 600; color: var(--fp-dusk);
}

/* Signed-in state */
.fp-status {
  display: inline-flex; align-items: center; gap: 10px; margin-bottom: 22px;
  font-size: .9rem; font-weight: 600; color: #136B3E;
}
.fp-status::before {
  content: ""; width: 26px; height: 26px; border-radius: 50%;
  background: __ICON_CHECK__ center / 60% no-repeat, #1F9D5A;
}
.st-key-fp_signin [data-testid="stButton"] button {
  height: 52px; padding: 0 26px; border-radius: 12px; border: 1px solid var(--fp-field-line);
  background: #FFFFFF; color: var(--fp-ink); font-weight: 600;
}
.st-key-fp_signin [data-testid="stButton"] button:hover {
  border-color: var(--fp-signal); color: var(--fp-signal);
}

@media (prefers-reduced-motion: reduce) {
  .fp-flow { background-image: __FLOW_STATIC__; }
  .stApp * { transition: none !important; }
}

/* Stack the panels before the story gets cramped. */
@media (max-width: 900px) {
  .block-container { padding: 16px 16px 24px; }
  .st-key-fp_shell { border-radius: 22px; }
  .st-key-fp_shell [data-testid="stHorizontalBlock"] { flex-direction: column; }
  .st-key-fp_shell [data-testid="stColumn"] {
    width: 100% !important; flex: 1 1 auto !important; min-width: 0;
  }
  .fp-story { min-height: 0; padding: 36px 24px 40px; }
  .fp-flow { margin-top: 32px; }
  .fp-headline { padding-top: 48px; }
  .st-key-fp_signin { min-height: 0; padding: 40px 24px 28px; }
  .fp-welcome p { margin-bottom: 32px; }
}
@media (max-width: 560px) {
  .fp-wordmark { font-size: 1.55rem; }
  .fp-tagline { font-size: .66rem; letter-spacing: .16em; }
  .fp-flow {
    aspect-ratio: auto; background: none; display: flex; flex-direction: column; gap: 10px;
  }
  .fp-node {
    position: static; width: 100%; transform: none; gap: 14px; padding: 12px 14px;
    border-radius: 14px;
  }
  .fp-node i { width: 36px; height: 36px; }
  .fp-node b { font-size: .98rem; }
  .fp-headline { font-size: 2rem; }
  .fp-welcome h1 { font-size: 2.3rem; }

  .fp-assure {
    flex-direction: row;
    flex-wrap: nowrap;
    justify-content: center;
    align-items: center;
    white-space: nowrap;
    font-size: .68rem;
    gap: 6px;
  }
  .fp-assure li + li::before {
    display: inline-block;
    flex-shrink: 0;
    margin: 0 2px;
  }

}
"""

_TOKENS = {
    "__NIGHT__": NIGHT,
    "__DUSK__": DUSK,
    "__SIGNAL__": SIGNAL,
    "__SKY__": SKY,
    "__INK__": INK,
    "__FONT__": FONT_STACK,
    "__SKYLINE__": SKYLINE,
    "__LOGO__": LOGO,
    "__FLOW_STATIC__": FLOW_STATIC,
    "__FLOW__": FLOW_ANIMATED,
    "__ICON_CAPTURE__": ICON_CAPTURE,
    "__ICON_QUALIFY__": ICON_QUALIFY,
    "__ICON_REVIEW__": ICON_REVIEW,
    "__ICON_LOCK__": ICON_LOCK,
    "__ICON_CHECK__": ICON_CHECK,
}


def stylesheet() -> str:
    css = _CSS
    for token, value in _TOKENS.items():
        css = css.replace(token, value)
    return f"<style>{css}</style>"


# --- Signed-in workspace (M5 page 2). Separate from the login stylesheet above, which it reuses
# tokens and artwork from but never overrides; app.py loads exactly one of the two per run.

_NAV_STROKE = "#C9D6FF"
NAV_HOME = _icon("<path d='M4 11.5 12 5l8 6.5'/><path d='M6.5 10v9h11v-9'/>", _NAV_STROKE)
NAV_LEADS = _icon(
    "<rect x='4' y='4.5' width='16' height='6.5' rx='2'/><rect x='4' y='13' width='16' "
    "height='6.5' rx='2'/><path d='M7.5 7.75h3M7.5 16.25h3'/>",
    "#FFFFFF",
)
NAV_DRAFT = _icon(
    "<path d='M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V7z'/>"
    "<path d='M14 3v4h4'/><path d='M9 12h6M9 16h4'/>",
    _NAV_STROKE,
)
NAV_OUTBOX = _icon("<path d='M21 3 3 10.5l7 2.5 2.5 7z'/><path d='m10 13 5-5'/>", _NAV_STROKE)
NAV_SETTINGS = _icon(
    "<path d='M4 7h9M17 7h3M4 17h3M11 17h9'/><circle cx='15' cy='7' r='2'/>"
    "<circle cx='9' cy='17' r='2'/>",
    _NAV_STROKE,
)
METRIC_USERS = _icon(
    "<circle cx='9' cy='9' r='3'/><path d='M3.5 19a5.5 5.5 0 0 1 11 0'/>"
    "<circle cx='16.5' cy='10' r='2.4'/><path d='M15.2 14.6a4.6 4.6 0 0 1 5.3 4.4'/>",
    "#2546CC",
)
METRIC_VALID = _icon("<path d='m5.5 12.5 4 4 9-9'/>", "#1F9D5A")
METRIC_PENDING = _icon("<circle cx='12' cy='12' r='8'/><path d='M12 7.5V12l3 2'/>", "#D98A00")
METRIC_DUPLICATE = _icon(
    "<circle cx='12' cy='12' r='8'/><path d='m9.2 9.2 5.6 5.6M14.8 9.2l-5.6 5.6'/>", "#D92D20"
)

METRIC_NEW = _icon("<path d='M12 5v14M5 12h14'/>", "#1F9D5A")
METRIC_HOT = _icon(
    "<path d='M12 21c3.9 0 6.5-2.6 6.5-6.2 0-3.4-2.4-5.4-3.6-8.3-.9 1.8-1.9 2.8-3.1 3.3"
    ".2-2.4-.6-4.6-2.3-6.8-.4 3.3-4 5.8-4 10.1C5.5 18.4 8.1 21 12 21z'/>",
    "#D92D20",
)
METRIC_WARM = _icon(
    "<path d='M12 21c3.9 0 6.5-2.6 6.5-6.2 0-3.4-2.4-5.4-3.6-8.3-.9 1.8-1.9 2.8-3.1 3.3"
    ".2-2.4-.6-4.6-2.3-6.8-.4 3.3-4 5.8-4 10.1C5.5 18.4 8.1 21 12 21z'/>",
    "#D98A00",
)
METRIC_REVIEW = _icon("<circle cx='12' cy='12' r='8'/><path d='M12 7.5V12l3 2'/>", "#6D4AE0")
ACT_GREEN = _icon("<path d='m5.5 12.5 4 4 9-9'/>", "#1F9D5A")
ACT_BLUE = _icon(
    "<path d='M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V7z'/>"
    "<path d='M14 3v4h4'/><path d='M9 12h6M9 16h4'/>",
    "#2546CC",
)
ACT_RED = _icon("<path d='m7 7 10 10M17 7 7 17'/>", "#D92D20")

_WORKSPACE_CSS = """
:root {
  --fp-ink: __INK__;
  --fp-text: #1E2A47;
  --fp-muted: #5E6A85;
  --fp-line: #E3E8F1;
  --fp-soft-line: #EEF1F6;
  --fp-blue: #2546CC;
  --fp-card-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 8px 20px -14px rgba(16, 24, 40, .18);
}
.stApp *:not([data-testid="stIconMaterial"]) { font-family: __FONT__; }
.stApp { background: #F3F6FB; }
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"], [data-testid="InputInstructions"] { display: none; }
.block-container { max-width: 1320px; padding: 28px 32px 40px; }

/* Sidebar */
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0F2160 0%, #0A1846 50%, #071236 100%);
  border-right: 1px solid rgba(255, 255, 255, .06);
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(.fp-side-foot) {
  gap: 6px; min-height: calc(100vh - 110px);
}
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.fp-side-foot) {
  margin-top: auto;
}
.fp-side-brand { display: flex; align-items: center; gap: 12px; padding: 0 10px 24px; }
[class*="st-key-fp_nav_"] button {
  justify-content: flex-start; gap: 14px; min-height: 46px; padding: 0 14px; border-radius: 10px;
  border: 0; background: transparent; color: rgba(225, 232, 255, .9); box-shadow: none;
}
[class*="st-key-fp_nav_"] button p { font-size: 1rem; }
[class*="st-key-fp_nav_"] button::before {
  content: ""; width: 22px; height: 22px; flex: none; background: var(--fp-ic) center / contain
    no-repeat;
}
.st-key-fp_nav_dashboard button { --fp-ic: __NAV_HOME__; }
.st-key-fp_nav_leads button { --fp-ic: __NAV_LEADS__; }
.st-key-fp_nav_drafts button { --fp-ic: __NAV_DRAFT__; }
.st-key-fp_nav_outbox button { --fp-ic: __NAV_OUTBOX__; }
.st-key-fp_nav_settings button { --fp-ic: __NAV_SETTINGS__; }
[class*="st-key-fp_nav_"] button:hover { background: rgba(255, 255, 255, .06); color: #FFFFFF; }
[class*="st-key-fp_nav_"] button:focus-visible { outline: 2px solid __SKY__; outline-offset: 2px; }
[class*="st-key-fp_nav_"] [data-testid="stBaseButton-primary"] {
  background: linear-gradient(180deg, #2B55E6, #2147C9); color: #FFFFFF; font-weight: 500;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .14), 0 10px 22px -12px rgba(37, 70, 204, .95);
}
.fp-mark { width: 34px; height: 34px; flex: none; background: __LOGO__ center / contain no-repeat; }
.fp-wordmark {
  font-size: 1.45rem; font-weight: 600; letter-spacing: -.02em; line-height: 1; color: #FFFFFF;
}
.fp-wordmark span { color: __SKY__; }
.fp-nav { display: flex; flex-direction: column; gap: 6px; margin-top: 0; }
.fp-nav-item {
  display: flex; align-items: center; gap: 14px; padding: 11px 14px; border-radius: 10px;
  font-size: 1rem; color: rgba(225, 232, 255, .9);
}
.fp-nav-item.is-active {
  background: linear-gradient(180deg, #2B55E6, #2147C9); color: #FFFFFF; font-weight: 500;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .14), 0 10px 22px -12px rgba(37, 70, 204, .95);
}
.fp-nav-item[aria-disabled="true"] { color: rgba(214, 224, 250, .58); cursor: not-allowed; }
.fp-ic {
  width: 22px; height: 22px; flex: none; background: var(--fp-ic) center / contain no-repeat;
}
.fp-nav-item[aria-disabled="true"] .fp-ic { opacity: .6; }
.fp-ic-home { --fp-ic: __NAV_HOME__; }
.fp-ic-leads { --fp-ic: __NAV_LEADS__; }
.fp-ic-draft { --fp-ic: __NAV_DRAFT__; }
.fp-ic-outbox { --fp-ic: __NAV_OUTBOX__; }
.fp-ic-settings { --fp-ic: __NAV_SETTINGS__; }
.fp-soon {
  margin-left: auto; padding: 1px 7px; border-radius: 999px; font-style: normal;
  font-size: .66rem; font-weight: 600; color: rgba(214, 224, 250, .72);
  border: 1px solid rgba(214, 224, 250, .22);
}
.fp-side-foot {
  margin-top: auto; padding: 22px 6px 4px; border-top: 1px solid rgba(255, 255, 255, .1);
  text-align: center;
}
.fp-side-foot .fp-credit { margin-top: 0; }
.fp-credit {
  display: flex; flex-direction: column; align-items: center; gap: 5px;
  margin: 16px 0 0; text-align: center;
}
.fp-credit .fp-credit-intro, .fp-credit .fp-credit-role {
  margin: 0; font-size: .74rem; line-height: 1.35; color: rgba(190, 204, 240, .66);
  text-wrap: balance;
}
.fp-credit .fp-credit-name {
  margin: 0; font-size: .86rem; line-height: 1.3; font-weight: 600; color: #FFFFFF;
}

/* Header */
.fp-mobilebar { display: none; }
.st-key-fp_head { justify-content: space-between; gap: 16px; }
.fp-title h1 {
  margin: 0; padding: 0; font-size: 2rem; font-weight: 700; letter-spacing: -.025em;
  line-height: 1.15; color: var(--fp-ink);
}
.fp-title p { margin: 4px 0 0; font-size: 1.02rem; line-height: 1.45; color: var(--fp-muted); }
.fp-title .fp-sample-note {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin-top: 12px;
  font-size: .84rem;
}
.fp-sample {
  padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
  color: #1F3FBF; background: #EAF0FF; border: 1px solid #D3DEFF;
}
.st-key-fp_head_actions { gap: 12px; }
.st-key-fp_operator button {
  border: 0; background: transparent; box-shadow: none; font-weight: 600; color: var(--fp-ink);
}
.st-key-fp_add_lead button {
  min-height: 44px; padding: 0 18px; border-radius: 10px; font-weight: 600; color: #FFFFFF;
  border: 1px solid #1D3FB8; background: linear-gradient(180deg, #2B55E6, #1F45C8);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .16), 0 10px 20px -12px rgba(37, 70, 204, .9);
}
.st-key-fp_add_lead button:hover { filter: brightness(1.07); }
.st-key-fp_add_lead button:focus-visible,
.st-key-fp_operator button:focus-visible { outline: 2px solid var(--fp-blue); outline-offset: 2px; }
.fp-who { margin: 0 0 10px; font-size: .9rem; line-height: 1.5; color: var(--fp-muted); }
.fp-who strong { color: var(--fp-ink); }
.fp-pop-title { margin: 0 0 6px; font-size: .98rem; font-weight: 600; color: var(--fp-ink); }
.fp-pop-copy { margin: 0; font-size: .88rem; line-height: 1.5; color: var(--fp-muted); }
.fp-pop-copy code { font-size: .82rem; }
.fp-details {
  display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; margin: 6px 0 12px;
  font-size: .88rem;
}
.fp-details dt { color: var(--fp-muted); }
.fp-details dd { margin: 0; color: var(--fp-text); overflow-wrap: anywhere; }

/* Summary cards */
.fp-metrics {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; margin: 6px 0 4px;
}
.fp-metric {
  display: flex; align-items: center; gap: 18px; min-height: 84px; padding: 18px 22px;
  background: #FFFFFF; border: 1px solid var(--fp-line); border-radius: 14px;
  box-shadow: var(--fp-card-shadow);
}
.fp-metric-ic {
  width: 52px; height: 52px; flex: none; border-radius: 50%;
  background: var(--fp-ic) center / 24px no-repeat, var(--fp-tint);
}
.fp-metric strong {
  display: block; font-size: 1.6rem; font-weight: 700; line-height: 1.1; color: var(--fp-ink);
}
.fp-metric span { font-size: .9rem; color: var(--fp-muted); }
.fp-tone-blue { --fp-tint: #EAF0FF; --fp-ic: __METRIC_USERS__; }
.fp-tone-green { --fp-tint: #E7F7EE; --fp-ic: __METRIC_VALID__; }
.fp-tone-amber { --fp-tint: #FFF5DE; --fp-ic: __METRIC_PENDING__; }
.fp-tone-red { --fp-tint: #FDECEC; --fp-ic: __METRIC_DUPLICATE__; }

/* Filters and table share one card */
.st-key-fp_card {
  padding: 18px 20px 16px; background: #FFFFFF; border: 1px solid var(--fp-line);
  border-radius: 14px; box-shadow: var(--fp-card-shadow);
}
.st-key-fp_card [data-testid="stTextInputRootElement"],
.st-key-fp_card [data-baseweb="select"] > div,
.st-key-fp_card [data-testid="stDateInput"] [data-baseweb="input"] {
  min-height: 42px; border-radius: 10px; border-color: #DCE2EE; background: #FFFFFF;
}
.st-key-fp_table {
  gap: 0; margin-top: 4px; border: 1px solid #E6EAF2; border-radius: 12px; overflow-x: auto;
}
.st-key-fp_table [data-testid="stHorizontalBlock"] {
  min-width: 860px; min-height: 52px; padding: 0 12px; gap: 8px;
  border-bottom: 1px solid var(--fp-soft-line);
}
.st-key-fp_table [data-testid="stHorizontalBlock"]:has(.fp-row-head) { background: #F7F9FC; }
.st-key-fp_table [class*="st-key-fp_lead_"]:hover { background: #FAFBFE; }
.fp-row {
  display: grid; align-items: center; gap: 12px; font-size: .92rem; color: var(--fp-text);
  grid-template-columns: 34px minmax(0, 1.15fr) minmax(0, 1.2fr) minmax(0, 1.6fr)
    minmax(0, .8fr) minmax(0, .85fr) minmax(0, .6fr) minmax(0, 1fr);
}
.fp-row-head, .fp-actions-head { font-size: .82rem; font-weight: 600; color: #3D4966; }
.fp-actions-head { text-align: center; }
.fp-row .c-num { text-align: center; color: #6B7896; }
.fp-row .c-name { font-weight: 600; color: var(--fp-ink); }
.fp-row-head .c-name { font-weight: 600; color: #3D4966; }
.fp-row .c-contact { overflow-wrap: anywhere; }
.fp-row .fp-email { color: #1F4FD8; }
.fp-badge, .fp-pill {
  display: inline-block; border: 1px solid; border-radius: 6px; font-size: .8rem; font-weight: 600;
  line-height: 1.5;
}
.fp-badge { padding: 1px 10px; }
.fp-pill { min-width: 36px; padding: 1px 8px; text-align: center; font-weight: 700; }
.fp-valid, .fp-high { color: #137A3E; background: #E7F7EE; border-color: #BFE8CF; }
.fp-info { color: #1F3FBF; background: #EAF0FF; border-color: #D3DEFF; }
.fp-neutral { color: #475467; background: #F2F4F7; border-color: #E4E7EC; }
.fp-pending, .fp-medium { color: #8A5600; background: #FFF5DE; border-color: #F3D894; }
.fp-duplicate, .fp-low { color: #B42318; background: #FDECEC; border-color: #F7C8C4; }
[class*="st-key-fp_actions_"] button {
  min-height: 32px; padding: 0 8px; border: 0; background: transparent; box-shadow: none;
}
.fp-empty { padding: 28px 12px 8px; text-align: center; }

.st-key-fp_tfoot { justify-content: space-between; padding: 14px 4px 0; gap: 12px; }
.fp-tfoot-note { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
.fp-count { font-size: .9rem; color: var(--fp-muted); }
.fp-selected {
  padding: 2px 9px; border-radius: 999px; font-size: .78rem; font-weight: 600;
  color: #1F3FBF; background: #EAF0FF;
}
.st-key-fp_pager { gap: 8px; align-items: center; }
.st-key-fp_pager button {
  width: 36px; min-height: 36px; padding: 0; border-radius: 8px; border-color: #DCE2EE;
}
.fp-page {
  display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px;
  border-radius: 8px; font-size: .9rem; font-weight: 600; color: #FFFFFF;
  background: var(--fp-blue);
}

/* Dashboard */
.fp-live {
  padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
  color: #137A3E; background: #E7F7EE; border: 1px solid #BFE8CF;
}
.fp-kpis .fp-tone-green { --fp-ic: __METRIC_NEW__; }
.fp-kpis .fp-tone-red { --fp-ic: __METRIC_HOT__; }
.fp-kpis .fp-tone-amber { --fp-ic: __METRIC_PENDING__; }
.fp-metric small { display: block; margin-top: 4px; font-size: .78rem; color: #8A94AB; }
.fp-grid { display: grid; gap: 18px; margin-top: 2px; }
.fp-grid-charts { grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr) minmax(0, 1fr); }
.fp-grid-tables { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.fp-panel {
  min-width: 0; padding: 18px 20px; background: #FFFFFF; border: 1px solid var(--fp-line);
  border-radius: 14px; box-shadow: var(--fp-card-shadow);
}
.fp-panel h2 {
  margin: 0; padding: 0; font-size: 1.08rem; font-weight: 700; letter-spacing: -.01em;
  color: var(--fp-ink);
}
.fp-panel > p { margin: 3px 0 14px; font-size: .86rem; color: var(--fp-muted); }
.fp-panel-empty { margin: 8px 0 4px; font-size: .9rem; color: var(--fp-muted); }
.fp-panel-foot { margin: 10px 0 0; font-size: .84rem; color: var(--fp-muted); }
.fp-bars {
  display: grid; grid-template-columns: repeat(14, minmax(0, 1fr)); align-items: end; gap: 6px;
  height: 190px; padding-top: 8px; border-bottom: 1px solid var(--fp-soft-line);
}
.fp-bar { display: flex; flex-direction: column; align-items: center; height: 100%; }
.fp-bar em {
  margin-top: auto; font-style: normal; font-size: .72rem; font-weight: 600; color: #3D4966;
}
.fp-bar span {
  width: 70%; max-width: 26px; margin-top: 3px; border-radius: 5px 5px 0 0;
  background: linear-gradient(180deg, #4D78F0, #2546CC);
}
.fp-bar small {
  height: 0; overflow: visible; margin-top: 6px; white-space: nowrap; font-size: .7rem;
  color: #6B7896;
}
.fp-bars { margin-bottom: 22px; }
.fp-donut-wrap { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
.fp-donut {
  position: relative; width: 150px; height: 150px; flex: none; border-radius: 50%;
}
.fp-donut > div {
  position: absolute; inset: 22px; display: flex; flex-direction: column; align-items: center;
  justify-content: center; border-radius: 50%; background: #FFFFFF;
}
.fp-donut strong { font-size: 1.5rem; font-weight: 700; color: var(--fp-ink); line-height: 1.1; }
.fp-donut span { font-size: .76rem; color: var(--fp-muted); }
.fp-legend {
  flex: 1; min-width: 140px; margin: 0; padding: 0; list-style: none; display: grid; gap: 8px;
  font-size: .86rem;
}
.fp-legend li { display: flex; align-items: center; gap: 8px; color: var(--fp-text); }
.fp-legend i { width: 10px; height: 10px; flex: none; border-radius: 50%; }
.fp-legend b { margin-left: auto; font-weight: 600; }
.fp-legend em { font-style: normal; color: var(--fp-muted); font-size: .8rem; }
.fp-table-scroll { overflow-x: auto; border: 1px solid #E6EAF2; border-radius: 10px; }
.fp-dtable { width: 100%; min-width: 520px; border-collapse: collapse; font-size: .88rem; }
.fp-dtable th {
  padding: 10px 12px; text-align: left; font-size: .8rem; font-weight: 600; color: #3D4966;
  background: #F7F9FC; border-bottom: 1px solid var(--fp-soft-line);
}
.fp-dtable td {
  padding: 11px 12px; color: var(--fp-text); border-bottom: 1px solid var(--fp-soft-line);
}
.fp-dtable tr:last-child td { border-bottom: 0; }
.fp-dtable strong { color: var(--fp-ink); font-weight: 600; }
.fp-muted { font-size: .8rem; color: #8A94AB; }
.fp-panel-wide { margin-top: 2px; }
.fp-activity {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;
  margin: 0; padding: 0; list-style: none;
}
.fp-activity li { display: flex; gap: 12px; align-items: flex-start; }
.fp-activity i {
  width: 40px; height: 40px; flex: none; border-radius: 50%;
  background: var(--fp-ic) center / 20px no-repeat, var(--fp-tint);
}
.fp-act-green i { --fp-tint: #E7F7EE; --fp-ic: __ACT_GREEN__; }
.fp-act-blue i { --fp-tint: #EAF0FF; --fp-ic: __ACT_BLUE__; }
.fp-act-red i { --fp-tint: #FDECEC; --fp-ic: __ACT_RED__; }
.fp-activity strong { display: block; font-size: .88rem; color: var(--fp-ink); }
.fp-activity span, .fp-activity time { display: block; font-size: .8rem; color: var(--fp-muted); }
.fp-notice {
  padding: 16px 18px; border-radius: 12px; border: 1px solid; background: #FFFFFF;
}
.fp-notice-error { border-color: #F7C8C4; background: #FFF8F7; }
.fp-notice-info { border-color: #D3DEFF; background: #F5F8FF; }
.fp-notice-success { border-color: #BFE8CF; background: #F3FBF6; }
.fp-errors { margin: 4px 0 0; padding-left: 18px; font-size: .88rem; color: #7A271A; }

/* Leads page */
.fp-tone-hot { --fp-tint: #FDECEC; --fp-ic: __METRIC_HOT__; }
.fp-tone-warm { --fp-tint: #FFF5DE; --fp-ic: __METRIC_WARM__; }
.fp-tone-review { --fp-tint: #F1ECFF; --fp-ic: __METRIC_REVIEW__; }
.st-key-fp_add_panel {
  padding: 18px 20px; background: #FFFFFF; border: 1px solid var(--fp-line);
  border-radius: 14px; box-shadow: var(--fp-card-shadow);
}
.fp-title .fp-section {
  margin: 0 0 4px; padding: 0; font-size: 1.15rem; font-weight: 700; color: var(--fp-ink);
}
.st-key-fp_add_panel [data-testid="stFormSubmitButton"] button {
  min-height: 42px; border-radius: 10px; font-weight: 600;
}
.fp-filter-note { margin: 0; font-size: .8rem; line-height: 1.4; color: var(--fp-muted); }
.fp-page-of { font-size: .86rem; font-weight: 600; color: var(--fp-text); white-space: nowrap; }
.fp-message { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 160px; overflow: auto; }
.st-key-fp_add_lead button {
  min-height: 44px; padding: 0 18px; border-radius: 10px; font-weight: 600;
}

/* Draft Responses page */
.fp-metrics-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.fp-metrics-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.fp-ob-note { margin-top: 14px; }

/* Settings page */
.fp-cli, .fp-plain { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.fp-cli li { display: grid; gap: 2px; }
.fp-cli code {
  justify-self: start; padding: 4px 8px; border-radius: 6px; font-size: .8rem;
  background: #F2F4F7; color: var(--fp-ink); overflow-wrap: anywhere;
}
.fp-cli span, .fp-plain li { font-size: .86rem; color: var(--fp-muted); }
.fp-plain li { padding-left: 14px; position: relative; }
.fp-plain li::before { content: "\2013"; position: absolute; left: 0; color: #B4BCCD; }
.st-key-fp_ob_filters { max-width: 260px; }
.st-key-fp_ob_list {
  gap: 8px; padding: 16px; background: #FFFFFF; border: 1px solid var(--fp-line);
  border-radius: 14px; box-shadow: var(--fp-card-shadow);
}
.st-key-fp_ob_list [role="radiogroup"] { gap: 8px; }
.st-key-fp_ob_list [role="radiogroup"] > label {
  width: 100%; margin: 0; padding: 10px 12px; border: 1px solid var(--fp-line);
  border-radius: 10px; background: #FFFFFF;
}
.st-key-fp_ob_list [role="radiogroup"] > label:has(input:checked) {
  border-color: var(--fp-blue); background: #F5F8FF; box-shadow: inset 3px 0 0 var(--fp-blue);
}
.st-key-fp_ob_list [role="radiogroup"] > label p {
  font-size: .86rem; line-height: 1.4; overflow-wrap: anywhere;
}
.st-key-fp_ob_pager { gap: 8px; justify-content: space-between; }
.st-key-fp_ob_pager button { width: 36px; min-height: 36px; padding: 0; border-radius: 8px; }
.st-key-fp_dr_queue {
  gap: 8px; padding: 16px; background: #FFFFFF; border: 1px solid var(--fp-line);
  border-radius: 14px; box-shadow: var(--fp-card-shadow);
}
.st-key-fp_dr_queue [role="radiogroup"] { gap: 8px; }
.st-key-fp_dr_queue [role="radiogroup"] > label {
  width: 100%; margin: 0; padding: 10px 12px; border: 1px solid var(--fp-line);
  border-radius: 10px; background: #FFFFFF; transition: border-color .12s ease;
}
.st-key-fp_dr_queue [role="radiogroup"] > label:hover { border-color: #B9C7EE; }
.st-key-fp_dr_queue [role="radiogroup"] > label:has(input:checked) {
  border-color: var(--fp-blue); background: #F5F8FF; box-shadow: inset 3px 0 0 var(--fp-blue);
}
.st-key-fp_dr_queue [role="radiogroup"] > label p { font-size: .86rem; line-height: 1.4; }
.fp-dr-head {
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start;
  gap: 10px; margin-bottom: 12px;
}
.fp-dr-head p { margin: 2px 0 0; font-size: .88rem; color: var(--fp-muted); }
.fp-dr-tags { display: flex; gap: 8px; align-items: center; }
.fp-dr-label {
  margin: 12px 0 4px; font-size: .78rem; font-weight: 600; letter-spacing: .01em;
  color: #3D4966;
}
.fp-dr-message, .fp-dr-body {
  margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .92rem;
  line-height: 1.6; color: var(--fp-text);
}
.fp-dr-message { max-height: 180px; overflow: auto; color: var(--fp-muted); }
.fp-dr-subject { margin: 0; font-weight: 600; color: var(--fp-ink); overflow-wrap: anywhere; }
.fp-dr-body {
  padding: 14px 16px; border-radius: 10px; background: #F7F9FC; border: 1px solid #E6EAF2;
}
.fp-dr-old { color: var(--fp-muted); }
.fp-dr-assess {
  margin-top: 12px; padding: 12px 14px; border-radius: 10px; background: #F5F8FF;
  border: 1px solid #D3DEFF;
}
.fp-dr-assess p { margin: 0; font-size: .88rem; line-height: 1.5; color: var(--fp-text); }
.fp-dr-assess .fp-dr-label { margin-top: 0; }
.fp-dr-flags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.fp-dr-warn { margin-top: 8px !important; font-weight: 600; color: #B42318 !important; }
.fp-dr-context, .fp-dr-draft { margin-bottom: 4px; }
.st-key-fp_dr_decide { gap: 10px; }
.st-key-fp_dr_decide button { min-height: 42px; border-radius: 10px; font-weight: 600; }

@media (max-width: 1280px) {
  .fp-grid-charts { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .fp-grid-charts > .fp-panel:first-child { grid-column: 1 / -1; }
}
@media (max-width: 980px) {
  .fp-grid-tables, .fp-grid-charts { grid-template-columns: minmax(0, 1fr); }
}

@media (max-width: 1100px) {
  .fp-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .block-container { padding: 12px 14px 32px; }
  .fp-mobilebar {
    display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-radius: 14px;
    background: linear-gradient(135deg, #0F2160, #071236);
  }
  .fp-mobilebar .fp-mark { width: 28px; height: 28px; }
  .fp-mobilebar .fp-wordmark { font-size: 1.2rem; }
  .fp-title h1 { font-size: 1.6rem; }
  .fp-title p { font-size: .92rem; }
  .fp-metrics { gap: 10px; }
  .fp-metric { gap: 12px; min-height: 0; padding: 12px; }
  .fp-metric-ic { width: 40px; height: 40px; background-size: 20px, auto; }
  .fp-metric strong { font-size: 1.3rem; }
  .fp-metric span { font-size: .78rem; }
  .st-key-fp_card { padding: 14px 12px; }
  .st-key-fp_table { overflow-x: visible; }
  .st-key-fp_table [data-testid="stHorizontalBlock"] {
    min-width: 0; padding: 12px 6px; align-items: flex-start;
  }
  .st-key-fp_table [data-testid="stHorizontalBlock"]:has(.fp-row-head) { display: none; }
  .fp-row {
    grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; font-size: .88rem;
    grid-template-areas: "name status" "company source" "contact contact" "created score";
  }
  .fp-row .c-num { display: none; }
  .fp-row .c-name { grid-area: name; }
  .fp-row .c-company { grid-area: company; color: var(--fp-muted); }
  .fp-row .c-contact { grid-area: contact; }
  .fp-row .c-source { grid-area: source; justify-self: end; color: var(--fp-muted); }
  .fp-row .c-status { grid-area: status; justify-self: end; }
  .fp-row .c-score { grid-area: score; justify-self: end; }
  .fp-row .c-created { grid-area: created; font-size: .82rem; color: var(--fp-muted); }
}
@media (prefers-reduced-motion: reduce) {
  .stApp * { transition: none !important; }
}
"""

_WORKSPACE_TOKENS = {
    "__INK__": INK,
    "__SKY__": SKY,
    "__FONT__": FONT_STACK,
    "__LOGO__": LOGO,
    "__NAV_HOME__": NAV_HOME,
    "__NAV_LEADS__": NAV_LEADS,
    "__NAV_DRAFT__": NAV_DRAFT,
    "__NAV_OUTBOX__": NAV_OUTBOX,
    "__NAV_SETTINGS__": NAV_SETTINGS,
    "__METRIC_USERS__": METRIC_USERS,
    "__METRIC_VALID__": METRIC_VALID,
    "__METRIC_PENDING__": METRIC_PENDING,
    "__METRIC_DUPLICATE__": METRIC_DUPLICATE,
    "__METRIC_NEW__": METRIC_NEW,
    "__METRIC_WARM__": METRIC_WARM,
    "__METRIC_REVIEW__": METRIC_REVIEW,
    "__METRIC_HOT__": METRIC_HOT,
    "__ACT_GREEN__": ACT_GREEN,
    "__ACT_BLUE__": ACT_BLUE,
    "__ACT_RED__": ACT_RED,
}


def workspace_stylesheet() -> str:
    css = _WORKSPACE_CSS
    for token, value in _WORKSPACE_TOKENS.items():
        css = css.replace(token, value)
    return f"<style>{css}</style>"
