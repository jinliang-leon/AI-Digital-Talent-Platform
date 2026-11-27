import pytest
from fastapi import HTTPException

from backend.app.modules.applications import transition_allowed
from backend.app.modules.files import validate_file


@pytest.mark.parametrize(("present", "target", "role", "closed", "allowed"), [
    ("collected", "pending_review", "hr", False, True),
    ("collected", "rejected", "hr", False, False),
    ("collected", "rejected", "hr", True, True),
    ("interviewing", "reserve", "hr", True, True),
    ("pending_interview", "interviewing", "interviewer", False, True),
    ("pending_review", "rejected", "interviewer", True, False),
    ("hired", "pending_review", "hr", True, False),
    ("reserve", "pending_review", "hr", True, True),
    ("pending_decision", "hired", "admin", False, False),
])
def test_transition_policy(present, target, role, closed, allowed):
    assert transition_allowed(present, target, role, closed) is allowed


@pytest.mark.parametrize(("filename", "contents", "accepted"), [
    ("resume.txt", "虚构样例简历".encode(), "text/plain"),
    ("resume.pdf", b"%PDF-1.7", "application/pdf"),
    ("resume.jpg", b"\xff\xd8\xff\xe0", "image/jpeg"),
    ("resume.png", b"\x89PNG\r\n\x1a\n", "image/png"),
    ("resume.pdf", b"not a pdf", None),
    ("resume.txt", b"ab\x00cd", None),
    ("resume.docx", b"not a docx", None),
    ("resume.html", b"<html>", None),
])
def test_file_signature(filename, contents, accepted):
    if accepted:
        assert validate_file(filename, contents) == accepted
    else:
        with pytest.raises(HTTPException) as fault:
            validate_file(filename, contents)
        assert fault.value.status_code == 415
