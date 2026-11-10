from app.services.indexing import factual_text


def test_notes_and_undeclared_fields_never_enter_index_text():
    facts = {
        "name": "李四",
        "skills": ["Python"],
        "projects": ["API服务"],
        "experience_months": 24,
        "notes": "绝密HR备注",
        "is_admin": True,
        "education": [],
        "experiences": [],
    }
    text = factual_text(facts)
    assert "Python" in text and "API服务" in text
    assert "绝密" not in text and "is_admin" not in text
