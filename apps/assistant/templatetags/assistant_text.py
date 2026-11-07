import re

from django import template
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

register = template.Library()

BOLD = re.compile(r"\*\*(.+?)\*\*")
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def inline(text: str) -> str:
    return BOLD.sub(r"<strong>\1</strong>", escape(text))


@register.filter
def answer_html(text: str) -> SafeString:
    """Model output to HTML: escaped first, then paragraphs, bullet lists and **bold** only."""
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(BULLET.match(line) for line in lines):
            items = "".join(f"<li>{inline(BULLET.sub('', line))}</li>" for line in lines)
            blocks.append(f"<ul>{items}</ul>")
        else:
            blocks.append(f"<p>{'<br>'.join(inline(line.strip()) for line in lines)}</p>")
    # safe: every piece of model text above went through escape()
    return mark_safe("".join(blocks))
