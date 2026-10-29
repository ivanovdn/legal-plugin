"""scripts/check_docs_links.py — docs/ stays one linked graph.

Each test builds a small repo in tmp_path, so the checker is exercised on
known-good and known-bad links rather than on whatever docs/ holds today.
"""
import subprocess
import textwrap

from scripts.check_docs_links import (
    BAD_ANCHOR, MISSING_FILE, ORPHAN, WIKILINK, check_docs, github_slug, heading_slugs, main,
)


def make_repo(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


def found(report, kind):
    return [(p.path, p.line) for p in report.problems if p.kind == kind]


# Two docs that link each other, so neither is an orphan; tests add a third.
HUB = {
    "docs/wiki.md": "# Wiki\n\n- [a](a.md)\n",
    "docs/a.md": "# A\n\nBack to the [wiki](wiki.md).\n",
}


def test_a_linked_graph_has_no_problems(tmp_path):
    repo = make_repo(tmp_path, HUB)
    report = check_docs(repo)
    assert report.problems == []
    assert report.files == ["docs/a.md", "docs/wiki.md"]
    assert report.links == 2


def test_a_link_to_a_file_that_does_not_exist(tmp_path):
    repo = make_repo(tmp_path, {**HUB, "docs/a.md": "# A\n\n[wiki](wiki.md)\n\nSee [gone](gone.md).\n"})
    assert found(check_docs(repo), MISSING_FILE) == [("docs/a.md", 5)]


def test_a_link_must_match_the_file_name_case(tmp_path):
    # macOS finds Wiki.md for wiki.md; GitHub and Linux do not.
    repo = make_repo(tmp_path, {**HUB, "docs/a.md": "# A\n\n[wiki](Wiki.md)\n"})
    [problem] = [p for p in check_docs(repo).problems if p.kind == MISSING_FILE]
    assert (problem.path, problem.line) == ("docs/a.md", 3)


def test_links_may_leave_docs_but_not_the_repo(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        **HUB,
        "CLAUDE.md": "# Rules\n",
        "src/app.py": "print()\n",
        "docs/a.md": "# A\n\n[wiki](wiki.md) [rules](../CLAUDE.md#rules) [app](../src/app.py)\n\n[out](../../outside.md)\n",
    })
    (tmp_path / "outside.md").write_text("# Outside\n")
    [problem] = [p for p in check_docs(repo).problems if p.kind == MISSING_FILE]
    assert (problem.path, problem.line) == ("docs/a.md", 5)
    assert "outside the repo" in problem.detail


def test_a_git_ignored_target_is_not_in_the_repo(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        ".gitignore": "data/\n",
        "data/notes.md": "# Notes\n",
        "docs/a.md": "# A\n\n[wiki](wiki.md)\n\n[notes](../data/notes.md)\n",
    })
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    [problem] = check_docs(repo).problems
    assert (problem.kind, problem.path, problem.line) == (MISSING_FILE, "docs/a.md", 5)
    assert "git-ignored" in problem.detail


def test_github_slug():
    assert github_slug("Legal Plugin — Architecture") == "legal-plugin--architecture"
    assert github_slug("Docker Services (docker-compose.yml)") == "docker-services-docker-composeyml"
    assert github_slug("snake_case & kebab-case") == "snake_case--kebab-case"
    assert github_slug("Ünïcode → Кирилиця") == "ünïcode--кирилиця"


def test_heading_slugs_read_the_rendered_heading_text():
    text = textwrap.dedent("""
        # Per-turn record — `turn_log`
        ## Notes
        ## Notes
        ### *Emphasis*, _more_ and [a link](#notes) ##
        #not-a-heading
        ```
        ## Inside a fence
        ```
    """)
    assert heading_slugs(text) == {
        "per-turn-record--turn_log", "notes", "notes-1", "emphasis-more-and-a-link",
    }


def test_anchors_must_match_a_heading(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        "docs/a.md": """
            # A

            [wiki](wiki.md) [ok](wiki.md#wiki) [ok](#a) [ok](#b-%C3%BC) [ok](#b-ü)

            [bad](wiki.md#nope)
            [bad](wiki.md#Wiki)
            [bad](#fenced)

            ## B ü

            ```
            ## Fenced
            ```
        """,
    })
    assert found(check_docs(repo), BAD_ANCHOR) == [("docs/a.md", 5), ("docs/a.md", 6), ("docs/a.md", 7)]


def test_anchors_on_files_other_than_markdown_are_not_headings(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        "src/app.py": "print()\n",
        "docs/a.md": "# A\n\n[wiki](wiki.md) [line](../src/app.py#L999)\n",
    })
    assert check_docs(repo).problems == []


def test_a_doc_no_other_doc_links_to_is_an_orphan(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        "docs/lonely.md": "# Lonely\n\n[wiki](wiki.md) [myself](lonely.md#lonely) [here](#lonely)\n",
    })
    assert found(check_docs(repo), ORPHAN) == [("docs/lonely.md", 0)]


def test_wikilinks_are_flagged_outside_code_only(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        "docs/a.md": """
            # A

            [wiki](wiki.md), per [[some-note]].
            A memory name in code: `[[some-note]]`; a regex: `\\[[^\\]]{0,40}\\]`.

            ```
            [[fenced-note]]
            ```
        """,
    })
    assert found(check_docs(repo), WIKILINK) == [("docs/a.md", 3)]


def test_links_inside_code_and_escaped_brackets_are_not_links(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        "docs/a.md": """
            # A

            [wiki](wiki.md) `[x](missing.md)` \\[y\\](missing.md) ``a `[z](missing.md)` b``

            ~~~markdown
            [w](missing.md)
            ~~~
        """,
    })
    report = check_docs(repo)
    assert report.problems == []
    assert report.links == 2  # wiki.md's link and the one in a.md


def test_external_links_are_not_checked_or_counted(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        "docs/a.md": "# A\n\n[wiki](wiki.md) [gh](https://github.com/x) [mail](mailto:a@b.c) [cdn](//cdn.x/y.js)\n",
    })
    report = check_docs(repo)
    assert report.problems == []
    assert report.links == 2


def test_link_destination_syntax(tmp_path):
    repo = make_repo(tmp_path, {
        **HUB,
        "docs/b c.md": "# B\n\n[wiki](wiki.md)\n",
        "docs/f(1).md": "# F\n\n[wiki](wiki.md)\n",
        "docs/a.md": """
            # A

            [wiki](wiki.md "The wiki") [`b c`](<b c.md>) [b](b%20c.md#b) [f](f(1).md) [[nested] text](wiki.md)
        """,
    })
    report = check_docs(repo)
    assert report.problems == []
    assert report.links == 8  # 5 in a.md, 1 each in wiki.md, b c.md and f(1).md


def test_main_exits_non_zero_on_a_problem(tmp_path, capsys):
    clean = make_repo(tmp_path / "clean", HUB)
    assert main(clean) == 0

    broken = make_repo(tmp_path / "broken", {**HUB, "docs/a.md": "# A\n\n[wiki](wiki.md) [gone](gone.md)\n"})
    assert main(broken) == 1
    out = capsys.readouterr()
    assert "docs/a.md:3: gone.md" in out.out
    assert "FAIL" in out.err
