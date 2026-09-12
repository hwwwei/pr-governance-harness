from app.diffs import added_lines, validate_candidate_patch
from app.providers import MockProvider


def test_added_lines_use_target_line_numbers_and_ignore_deletions():
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -10,2 +10,2 @@
-eval(old_value)
+safe_value = value.strip()
 context()
"""
    lines = added_lines(diff)
    assert [(line.file, line.line, line.text) for line in lines] == [("a.py", 10, "safe_value = value.strip()")]


def test_candidate_patch_is_parseable_and_scoped():
    findings = MockProvider().analyze("reliability", """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -4,0 +4,1 @@
+requests.get(url, verify=False)
""")['findings']
    patch = MockProvider().propose_patch(findings)
    result = validate_candidate_patch(patch, {"a.py"})
    assert result["parseable"] is True
    assert result["scope_allowed"] is True
    assert result["safe"] is True


def test_candidate_patch_rejects_path_traversal():
    patch = """diff --git a/../secrets.txt b/../secrets.txt
--- a/../secrets.txt
+++ b/../secrets.txt
@@ -1,1 +1,1 @@
-old
+new
"""
    result = validate_candidate_patch(patch, {"src/a.py"})
    assert result["scope_allowed"] is False
