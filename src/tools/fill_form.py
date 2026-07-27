r"""
form_filler_tool.py
====================
A plug-and-play set of tools for an OpenAI Agents SDK agent to open a job
application link, inspect its fields, and fill them in - either
automatically from a saved profile, or with values the LLM decides itself.

Works on:
  1. Google Forms (detected via URL / DOM structure, uses ARIA roles since
     Google Forms doesn't expose real <input> elements for most controls).
  2. Generic HTML forms (any site using standard <input>/<textarea>/<select>).

Tools exposed (add all of them to your agent):
  - list_form_fields(url)              -> inspect only, no filling
  - autofill_application_form(url)     -> auto-fill what matches the profile,
                                           report what's left for the LLM
  - fill_form_fields(url, values)      -> LLM fills specific fields by id
  - submit_form(url)                   -> explicit, separate submit action

How the pieces fit together:
  The browser session (and the extracted field handles) are cached in memory
  per URL, so calling list_form_fields / autofill_application_form once and
  then fill_form_fields afterward all operate on the SAME open page - the
  agent can inspect, decide, and fill across multiple tool calls instead of
  everything happening in one deterministic pass.

Design choices (read before using):
  - The browser is launched NON-headless and stays open across calls and
    after filling, so you can visually review before submitting.
  - submit_form is a separate, explicit tool. Nothing auto-submits. Tell your
    agent's instructions to only call submit_form when the user explicitly
    asks for THIS application to be submitted.
  - Matching (in autofill_application_form) is heuristic alias + fuzzy
    matching on field labels. It won't be perfect - that's why unmatched
    fields are handed back with full detail (id, type, options) for the LLM
    to fill via fill_form_fields.

Install:
    pip install playwright
    playwright install chrome
    (If you already have Google Chrome installed system-wide, Playwright
    will use that directly via channel="chrome" - the "playwright install
    chrome" step just makes sure a compatible copy is available.)

Google sign-in note:
    Google blocks sign-in attempts made *while* a CDP-automation tool (like
    Playwright) is driving the browser - this is a hard block, not a
    spoofable flag. So sign in OUTSIDE automation, once:
      1. Fully close Chrome.
      2. Manually launch Chrome pointed at the SAME profile folder this
         script uses (~/.applytrack_ai/browser_profile), e.g. on Windows:
         "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\Users\<you>\.applytrack_ai\browser_profile"
      3. Sign into Google normally in that window, then close Chrome.
      4. Run this script/tool as normal from then on - it reuses that
         already-authenticated profile and never triggers the sign-in
         block again (until the session eventually expires).

Plug into your agent:
    from agents import Agent
    from form_filler_tool import (
        list_form_fields,
        autofill_application_form,
        fill_form_fields,
        submit_form,
    )

    agent = Agent(
        name="ApplyTrack",
        instructions="...",
        tools=[..., list_form_fields, autofill_application_form, fill_form_fields, submit_form],
    )
"""

import asyncio
import difflib
import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, ElementHandle

from agents import function_tool


# ---------------------------------------------------------------------------
# 1. YOUR PROFILE - edit these values once, reused by autofill_application_form.
#    `overrides` in that tool call can patch any of these per-application.
# ---------------------------------------------------------------------------

APPLICANT_PROFILE: dict[str, str] = {
    "full_name": "Harsh Dharnidharka",
    "first_name": "Harsh",
    "last_name": "Dharnidharka",
    "email": "dharnidharkaharsh@gmail.com",
    "phone": "[REDACTED]",
    "linkedin_url": "https://www.linkedin.com/in/harsh-dharnidharka/",
    "github_url": "https://github.com/Harsh251005",
    "resume_path": r"C:\Users\harsh\Downloads\Harsh_Dharnidharka_Resume.pdf",  # absolute local path to your resume PDF, for file-upload fields
    "current_location": "Mumbai, India",
    "university": "University of Mumbai",
    "degree": "BSc IT",
    "graduation_year": "2026",
    "years_of_experience": "0",
    "cover_letter": "I'm an Applied AI Engineer focused on building agentic systems with LangGraph and the OpenAI Agents SDK. I'd love to bring that hands-on experience to your team.",
    "expected_salary": "",
    "notice_period": "Immediate",
}

# Persistent browser profile - sign into Google here ONCE (see note above).
BROWSER_PROFILE_DIR = Path.home() / ".applytrack_ai" / "browser_profile"
BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

AUTO_CHECK_KEYWORDS = ["agree", "consent", "terms", "privacy policy", "confirm"]

FIELD_ALIASES: dict[str, list[str]] = {
    "full_name": ["full name", "your name", "applicant name", "candidate name", "name"],
    "first_name": ["first name", "given name", "fname"],
    "last_name": ["last name", "surname", "family name", "lname"],
    "email": ["email address", "e-mail", "email id", "email"],
    "phone": ["phone number", "mobile number", "contact number", "whatsapp number", "phone", "mobile"],
    "linkedin_url": ["linkedin profile", "linkedin"],
    "github_url": ["github profile", "github"],
    "portfolio_url": ["portfolio link", "personal website", "portfolio", "website"],
    "resume_path": ["upload resume", "attach resume", "upload cv", "resume", "cv"],
    "current_location": ["current location", "where are you based", "city", "location", "address"],
    "university": ["university", "college name", "college", "institution", "school"],
    "degree": ["highest qualification", "qualification", "degree", "education"],
    "graduation_year": ["year of graduation", "graduation year", "passing year"],
    "years_of_experience": ["years of experience", "work experience", "total experience", "experience"],
    "cover_letter": [
        "why do you want to join", "why should we hire you", "tell us about yourself",
        "additional information", "cover letter", "message",
    ],
    "expected_salary": ["expected salary", "salary expectation", "ctc expected", "ctc"],
    "notice_period": ["notice period", "when can you join", "availability", "start date"],
}


# ---------------------------------------------------------------------------
# 2. Field model
# ---------------------------------------------------------------------------

@dataclass
class FormField:
    field_id: str
    label: str
    kind: str  # text | textarea | select | select_google | radio | checkbox_single | checkbox_group | file | unknown
    options: list[str] = dc_field(default_factory=list)
    handle: Optional[ElementHandle] = None
    option_handles: Optional[dict[str, ElementHandle]] = None


def _format_field_desc(f: FormField) -> str:
    opts = f" | options: {f.options}" if f.options else ""
    return f'  - {f.field_id} [{f.kind}] "{f.label}"{opts}'


# ---------------------------------------------------------------------------
# 3. Alias matching (used only by autofill_application_form)
# ---------------------------------------------------------------------------

def _match_field(label: str, profile: dict) -> Optional[str]:
    if not label:
        return None
    label_lc = label.strip().lower()

    best_key, best_score = None, 0
    for key, aliases in FIELD_ALIASES.items():
        if key not in profile or not str(profile.get(key, "")).strip():
            continue
        for alias in aliases:
            if alias in label_lc:
                score = len(alias)
                if score > best_score:
                    best_key, best_score = key, score
    if best_key:
        return best_key

    best_key, best_ratio = None, 0.0
    for key, aliases in FIELD_ALIASES.items():
        if key not in profile or not str(profile.get(key, "")).strip():
            continue
        for alias in aliases:
            ratio = difflib.SequenceMatcher(None, alias, label_lc).ratio()
            if ratio > best_ratio:
                best_key, best_ratio = key, ratio
    return best_key if best_ratio >= 0.6 else None


def _is_checkbox_autocheckable(label: str) -> bool:
    label_lc = label.lower()
    return any(kw in label_lc for kw in AUTO_CHECK_KEYWORDS)


# ---------------------------------------------------------------------------
# 4. Label extraction helpers
# ---------------------------------------------------------------------------

_LABEL_EXTRACT_JS = """
el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) {
        const lbl = document.querySelector(`label[for="${el.id}"]`);
        if (lbl && lbl.innerText.trim()) return lbl.innerText;
    }
    const closestLabel = el.closest('label');
    if (closestLabel && closestLabel.innerText.trim()) return closestLabel.innerText;
    if (el.placeholder) return el.placeholder;
    let prev = el.previousElementSibling;
    if (prev && prev.innerText && prev.innerText.trim().length < 120) return prev.innerText;
    if (el.name) return el.name.replace(/[_-]/g, ' ');
    return '';
}
"""

_GROUP_LABEL_JS = """
el => {
    const fs = el.closest('fieldset');
    if (fs) {
        const legend = fs.querySelector('legend');
        if (legend && legend.innerText.trim()) return legend.innerText;
    }
    return '';
}
"""


async def _get_label(element: ElementHandle) -> str:
    try:
        text = await element.evaluate(_LABEL_EXTRACT_JS)
        return (text or "").strip()
    except Exception:
        return ""


async def _get_group_label(element: ElementHandle) -> str:
    try:
        text = await element.evaluate(_GROUP_LABEL_JS)
        return (text or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 5. Field extraction - generic HTML forms
# ---------------------------------------------------------------------------

async def _extract_generic_fields(page: Page) -> list[FormField]:
    fields: list[FormField] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        fid = f"f{counter}"
        counter += 1
        return fid

    text_els = await page.query_selector_all(
        "input:not([type=hidden]):not([type=submit]):not([type=button])"
        ":not([type=checkbox]):not([type=radio]):not([type=file]), textarea"
    )
    for el in text_els:
        if not await el.is_visible():
            continue
        label = await _get_label(el)
        fields.append(FormField(next_id(), label or "(unlabeled text field)", "text", handle=el))

    for el in await page.query_selector_all("select"):
        if not await el.is_visible():
            continue
        label = await _get_label(el)
        try:
            options = await el.evaluate("el => Array.from(el.options).map(o => o.textContent.trim())")
        except Exception:
            options = []
        fields.append(FormField(next_id(), label or "(unlabeled dropdown)", "select", options=options, handle=el))

    for el in await page.query_selector_all("input[type=file]"):
        label = await _get_label(el)
        fields.append(FormField(next_id(), label or "(file upload)", "file", handle=el))

    radios = await page.query_selector_all("input[type=radio]")
    radio_groups: dict[str, list[ElementHandle]] = {}
    for el in radios:
        name = await el.get_attribute("name") or ""
        radio_groups.setdefault(name, []).append(el)
    for name, els in radio_groups.items():
        option_handles: dict[str, ElementHandle] = {}
        for el in els:
            opt_label = await _get_label(el) or (await el.get_attribute("value")) or "option"
            option_handles[opt_label] = el
        group_label = await _get_group_label(els[0]) or name or "(radio group)"
        fields.append(FormField(next_id(), group_label, "radio", options=list(option_handles.keys()),
                                 option_handles=option_handles))

    checkboxes = await page.query_selector_all("input[type=checkbox]")
    cb_groups: dict[str, list[ElementHandle]] = {}
    for el in checkboxes:
        name = await el.get_attribute("name") or f"__single_{id(el)}"
        cb_groups.setdefault(name, []).append(el)
    for name, els in cb_groups.items():
        if len(els) == 1:
            label = await _get_label(els[0])
            fields.append(FormField(next_id(), label or "(checkbox)", "checkbox_single", handle=els[0]))
        else:
            option_handles = {}
            for el in els:
                opt_label = await _get_label(el) or "option"
                option_handles[opt_label] = el
            group_label = await _get_group_label(els[0]) or name
            fields.append(FormField(next_id(), group_label, "checkbox_group", options=list(option_handles.keys()),
                                     option_handles=option_handles))

    return fields


# ---------------------------------------------------------------------------
# 6. Field extraction - Google Forms (ARIA-role based, not real <input>s)
# ---------------------------------------------------------------------------

async def _extract_google_fields(page: Page) -> list[FormField]:
    fields: list[FormField] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        fid = f"f{counter}"
        counter += 1
        return fid

    items = await page.query_selector_all('div[role="listitem"]')
    for item in items:
        heading = await item.query_selector('[role="heading"]')
        if not heading:
            continue
        label = (await heading.inner_text()).strip()
        if not label:
            continue

        text_input = await item.query_selector('input[type="text"], textarea')
        if text_input:
            fields.append(FormField(next_id(), label, "text", handle=text_input))
            continue

        radios = await item.query_selector_all('div[role="radio"]')
        if radios:
            option_handles = {}
            for r in radios:
                r_label = (await r.get_attribute("data-value")) or (await r.get_attribute("aria-label")) or (await r.inner_text())
                option_handles[(r_label or "option").strip()] = r
            fields.append(FormField(next_id(), label, "radio", options=list(option_handles.keys()),
                                     option_handles=option_handles))
            continue

        checks = await item.query_selector_all('div[role="checkbox"]')
        if checks:
            option_handles = {}
            for c in checks:
                c_label = (await c.get_attribute("data-answer-value")) or (await c.get_attribute("aria-label")) or (await c.inner_text())
                option_handles[(c_label or "option").strip()] = c
            fields.append(FormField(next_id(), label, "checkbox_group", options=list(option_handles.keys()),
                                     option_handles=option_handles))
            continue

        listbox = await item.query_selector('div[role="listbox"]')
        if listbox:
            options: list[str] = []
            try:
                await listbox.click()
                opts = await page.query_selector_all('div[role="option"]')
                for o in opts:
                    t = (await o.inner_text()).strip()
                    if t:
                        options.append(t)
                await page.keyboard.press("Escape")
            except Exception:
                pass
            fields.append(FormField(next_id(), label, "select_google", options=options, handle=listbox))
            continue

        fields.append(FormField(next_id(), label, "unknown", handle=item))

    return fields


async def _extract_fields(page: Page) -> list[FormField]:
    is_google_form = "docs.google.com/forms" in page.url or bool(
        await page.query_selector('div[role="listitem"]')
    )
    if is_google_form:
        return await _extract_google_fields(page)
    return await _extract_generic_fields(page)


# ---------------------------------------------------------------------------
# 7. Filling a single field
# ---------------------------------------------------------------------------

async def _fill_one_field(page: Page, f: FormField, value: str) -> tuple[bool, str]:
    try:
        if f.kind == "text":
            await f.handle.click()
            await f.handle.fill(value)
            return True, f"filled \"{f.label}\" with '{value}'"

        if f.kind == "select":
            try:
                await f.handle.select_option(label=value)
            except Exception:
                await f.handle.select_option(value=value)
            return True, f"selected '{value}' for \"{f.label}\""

        if f.kind == "select_google":
            await f.handle.click()
            opts = await page.query_selector_all('div[role="option"]')
            for o in opts:
                t = (await o.inner_text()).strip()
                if value.lower() in t.lower() or t.lower() in value.lower():
                    await o.click()
                    return True, f"selected '{t}' for \"{f.label}\""
            await page.keyboard.press("Escape")
            return False, f"no option matching '{value}' found for \"{f.label}\" (options: {f.options})"

        if f.kind == "file":
            await f.handle.set_input_files(value)
            return True, f"uploaded file for \"{f.label}\""

        if f.kind == "checkbox_single":
            truthy = value.strip().lower() in ("true", "yes", "1", "check", "checked")
            if truthy:
                await f.handle.check()
            else:
                await f.handle.uncheck()
            return True, f"set checkbox \"{f.label}\" to {truthy}"

        if f.kind in ("radio", "checkbox_group"):
            wanted = [v.strip().lower() for v in value.split(",") if v.strip()]
            matched_any = False
            for opt_label, handle in (f.option_handles or {}).items():
                if opt_label.lower() in wanted or any(w in opt_label.lower() for w in wanted):
                    await handle.click()
                    matched_any = True
                    if f.kind == "radio":
                        break
            if matched_any:
                return True, f"selected '{value}' for \"{f.label}\""
            return False, f"no option matching '{value}' for \"{f.label}\" (options: {f.options})"

        return False, f"unsupported field kind '{f.kind}' for \"{f.label}\""
    except Exception as e:
        return False, f"error filling \"{f.label}\": {e}"


async def _autofill(page: Page, fields: list[FormField], profile: dict) -> tuple[list[str], list[FormField]]:
    filled: list[str] = []
    unmatched: list[FormField] = []

    for f in fields:
        if f.kind in ("checkbox_single", "checkbox_group") and _is_checkbox_autocheckable(f.label):
            fill_value = "true" if f.kind == "checkbox_single" else ",".join(f.options)
            ok, msg = await _fill_one_field(page, f, fill_value)
            (filled if ok else unmatched).append(msg if ok else f)  # type: ignore
            continue

        key = _match_field(f.label, profile)
        value = str(profile.get(key, "")).strip() if key else ""
        if not value:
            unmatched.append(f)
            continue

        ok, msg = await _fill_one_field(page, f, value)
        if ok:
            filled.append(msg)
        else:
            unmatched.append(f)

    return filled, unmatched


# ---------------------------------------------------------------------------
# 8. Session management - keeps the SAME browser page + field handles alive
#    across separate tool calls (inspect now, fill later).
# ---------------------------------------------------------------------------

_SESSIONS: dict[str, dict] = {}
_FIELD_CACHE: dict[str, dict[str, FormField]] = {}


async def _get_session(url: str) -> Page:
    session = _SESSIONS.get(url)
    if session and not session["page"].is_closed():
        return session["page"]

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        str(BROWSER_PROFILE_DIR),
        headless=False,
        channel="chrome",
        viewport=None,
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)  # let JS-heavy forms (Google Forms) finish rendering

    _SESSIONS[url] = {"playwright": playwright, "context": context, "page": page}
    return page


async def _get_fields(url: str, page: Page, refresh: bool = False) -> dict[str, FormField]:
    if not refresh and url in _FIELD_CACHE:
        return _FIELD_CACHE[url]
    fields_list = await _extract_fields(page)
    fields = {f.field_id: f for f in fields_list}
    _FIELD_CACHE[url] = fields
    return fields


# ---------------------------------------------------------------------------
# 9. Agent-facing tools
# ---------------------------------------------------------------------------

@function_tool
async def list_form_fields(url: str) -> str:
    """
    Open (or reuse) the application form and list every field found -
    label, type, and options - without filling anything. Use this when you
    want to decide every field's value yourself instead of relying on the
    saved profile. Fill afterward with fill_form_fields.

    Args:
        url: The application form URL.
    """
    page = await _get_session(url)
    fields = await _get_fields(url, page, refresh=True)

    lines = [f"Found {len(fields)} field(s) on {url}:"]
    lines += [_format_field_desc(f) for f in fields.values()]
    lines.append('Call fill_form_fields(url, values) with JSON like \'{"f0": "value"}\' to fill them.')
    return "\n".join(lines)


@function_tool
async def autofill_application_form(url: str, overrides: Optional[str] = None) -> str:
    """
    Open the application form and auto-fill every field that can be
    confidently matched to the saved profile (name, email, phone, links,
    education, resume, consent checkboxes, etc). Fields it can't confidently
    match are reported back with their id/type/options so you can fill them
    yourself with fill_form_fields.

    Args:
        url: The application form URL.
        overrides: Optional JSON string to patch specific profile fields for
            this application, e.g. '{"cover_letter": "..."}'.
    """
    profile = dict(APPLICANT_PROFILE)
    if overrides:
        try:
            parsed = json.loads(overrides)
            if isinstance(parsed, dict):
                profile.update(parsed)
        except json.JSONDecodeError:
            pass

    page = await _get_session(url)
    fields = await _get_fields(url, page, refresh=True)
    filled, unmatched = await _autofill(page, list(fields.values()), profile)

    lines = [f"Auto-filled {len(filled)} field(s):"]
    lines += [f"  - {m}" for m in filled] or ["  (none)"]
    lines.append(f"\n{len(unmatched)} field(s) need your input - call fill_form_fields(url, values) with these ids:")
    lines += [_format_field_desc(f) for f in unmatched] or ["  (none)"]
    return "\n".join(lines)


@function_tool
async def fill_form_fields(url: str, values: str) -> str:
    """
    Fill specific form fields with values you decide, using the field_ids
    returned by list_form_fields or autofill_application_form.

    Args:
        url: The same application URL already opened/inspected.
        values: JSON string mapping field_id -> value, e.g.
            '{"f4": "I want to work here because...", "f6": "Yes"}'.
            For radio/checkbox_group fields, use the option's label text
            (from the "options" list you were shown). For checkbox_group
            with multiple selections, comma-separate the labels.
    """
    page = await _get_session(url)
    fields = await _get_fields(url, page)

    try:
        parsed = json.loads(values)
        if not isinstance(parsed, dict):
            return "'values' must be a JSON object mapping field_id -> value."
    except json.JSONDecodeError as e:
        return f"Could not parse 'values' as JSON: {e}"

    results = []
    for field_id, value in parsed.items():
        f = fields.get(field_id)
        if not f:
            results.append(f"  - {field_id}: unknown field_id (available: {list(fields.keys())})")
            continue
        ok, msg = await _fill_one_field(page, f, str(value))
        results.append(f"  - {msg}" if ok else f"  - FAILED: {msg}")

    return "Fill results:\n" + "\n".join(results)


@function_tool
async def submit_form(url: str) -> str:
    """
    Click the application form's submit button. Only call this when the
    user has explicitly asked to submit THIS specific application - never
    submit proactively just because filling finished.

    Args:
        url: The same application URL already opened/filled.
    """
    page = await _get_session(url)
    submit_btn = await page.query_selector(
        'button[type=submit], input[type=submit], div[role="button"]:has-text("Submit")'
    )
    if not submit_btn:
        return "No submit button found - please submit manually."
    await submit_btn.click()
    return "Submitted."


# ---------------------------------------------------------------------------
# 10. Standalone test
# ---------------------------------------------------------------------------

# if __name__ == "__main__":
#     test_url = "https://docs.google.com/forms/d/e/1FAIpQLSeT5mKeKVMA_KV7yFrW5Rl0NPQ56wwnI6KrStqH1_gvlelYRA/viewform"
#
#     async def _main():
#         page = await _get_session(test_url)
#         fields = await _get_fields(test_url, page, refresh=True)
#         for f in fields.values():
#             print(_format_field_desc(f))
#         filled, unmatched = await _autofill(page, list(fields.values()), APPLICANT_PROFILE)
#         print("\nFilled:", *filled, sep="\n  ")
#         print("\nUnmatched:", *[f.label for f in unmatched], sep="\n  ")
#         await asyncio.sleep(120)
#
#     asyncio.run(_main())