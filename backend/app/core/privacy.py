"""Project raw text into a privacy-filtered view without moving evidence offsets."""
import re

EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.UNICODE)
NUMBER = re.compile(r"(?<!\d)\+?(?:\d[ ()\-\t]?){10,17}[\dXx](?!\d)")
FORMATTED_PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[ -])?(?:\(\d{2,4}\)|\d{2,4})[ -]\d{3,4}[ -]\d{4}(?!\d)"
)
CONTACT_FIELD = re.compile(
    r"(?i)(?:家庭住址|联系地址|居住地址|地址|住址|姓名|身份证(?:号码|号)?|电话|手机(?:号码|号)|邮箱|微信"
    r"|(?<!\w)(?:home address|residential address|address|full name|candidate name|"
    r"e-?mail|mobile|phone(?: number)?|national id|id number))\s*[:：][^\n\r;；]+"
)
CHINESE_STREET = re.compile(
    r"[\u3400-\u9fff]{1,12}(?:省|市|区|县|镇|街道)"
    r"[\u3400-\u9fff0-9]{1,22}(?:路|街|巷|道)\s*\d{1,6}\s*(?:号|栋|弄|单元)"
    r"(?:\s*[\w-]{1,8}(?:室|栋|座|单元)){0,3}"
)
ENGLISH_STREET = re.compile(
    r"(?i)\b\d{1,6}[ \t]+(?:[\w.'-]{1,24}[ \t]+){1,5}"
    r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Lane|Ln\.?|Drive|Dr\.?|Boulevard|Blvd\.?|Court|Ct\.?)"
    r"\b(?:[ \t]+(?:Apt|Unit|Suite)[ \t#]+[\w-]{1,12})?"
)
DOCUMENT_DIRECTIVE = re.compile(
    r"(?i)\bignore\s+(?:all\s+)?(?:previous|prior|system|developer)\s+instructions\b"
    r"|\breveal\s+(?:the|your)\s+(?:system prompt|api key)\b"
    r"|(?:忽略|无视).{0,12}(?:系统|之前|以上|先前).{0,8}(?:指令|提示)"
)
PATTERNS = (EMAIL, NUMBER, FORMATTED_PHONE, CONTACT_FIELD, CHINESE_STREET, ENGLISH_STREET)


def mask_private(text: str) -> str:
    """Mask detected values and suspicious document lines, preserving all offsets."""
    chars = list(text)
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            chars[match.start():match.end()] = " " * (match.end() - match.start())
    for line in re.finditer(r"[^\r\n]+", text):
        if DOCUMENT_DIRECTIVE.search(line.group()):
            chars[line.start():line.end()] = " " * (line.end() - line.start())
    return "".join(chars)


def clean_model_text(text: str) -> str:
    return mask_private(text).strip()


def has_private(text: str) -> bool:
    return any(pattern.search(text) for pattern in PATTERNS) or DOCUMENT_DIRECTIVE.search(text) is not None
