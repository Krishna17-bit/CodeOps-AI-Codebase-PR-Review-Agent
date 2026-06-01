from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.code_chunks import make_chunks
from src.file_utils import find_repo_root, reset_dir, safe_extract_zip, write_uploaded_file
from src.llm_client import AIReasoningClient, answer_code_question, enrich_summary
from src.pr_reviewer import review_diff
from src.reporting import findings_to_df, make_markdown_report, save_exports
from src.static_analyzer import analyze_repo
from src.ui_styles import APP_CSS

BASE_DIR = Path(__file__).resolve().parent
SAMPLE_ZIP = BASE_DIR / "sample_data" / "sample_repo.zip"
SAMPLE_DIFF = BASE_DIR / "sample_data" / "sample_pr.diff"
WORK_DIR = BASE_DIR / "outputs" / "workspace"

st.set_page_config(
    page_title="CodeOps AI",
    page_icon="◼",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(APP_CSS, unsafe_allow_html=True)

# Extra local CSS for dark dependency graph tables and HTML tables.
# This is kept inside app.py so you do not need to edit src/ui_styles.py again.
st.markdown(
    """
    <style>
    .dark-table-scroll {
        width: 100%;
        overflow: auto;
        border: 1px solid #2a2a2a;
        border-radius: 16px;
        background: #101010;
        margin-top: 12px;
        margin-bottom: 18px;
    }

    .dark-table-scroll table {
        width: 100%;
        border-collapse: collapse;
        background: #101010 !important;
        color: #ffffff !important;
        font-size: 0.92rem;
    }

    .dark-table-scroll thead tr {
        background: #171717 !important;
    }

    .dark-table-scroll th {
        color: #ffffff !important;
        background: #171717 !important;
        border-bottom: 1px solid #2a2a2a !important;
        padding: 12px 14px;
        text-align: left;
        font-weight: 850;
        position: sticky;
        top: 0;
        z-index: 1;
        white-space: nowrap;
    }

    .dark-table-scroll td {
        color: #e8e8e8 !important;
        background: #101010 !important;
        border-bottom: 1px solid #242424 !important;
        padding: 11px 14px;
        vertical-align: top;
    }

    .dark-table-scroll tr:hover td {
        background: #151515 !important;
    }

    .dark-table-scroll pre,
    .dark-table-scroll code {
        color: #f7f7f7 !important;
        background: #151515 !important;
    }

    /* Make Graphviz container blend with dark UI */
    [data-testid="stGraphVizChart"] {
        background: #070707 !important;
        border: 1px solid #2a2a2a;
        border-radius: 16px;
        padding: 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def metric_card(label: str, value: str, note: str = "") -> None:
    st.markdown(
        f"""
        <div class='metric-card'>
            <div class='metric-label'>{label}</div>
            <div class='metric-value'>{value}</div>
            <div class='metric-note'>{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def severity_pill(severity: str) -> str:
    cls = "pill-ok"
    if severity.lower() in {"critical", "high"}:
        cls = "pill-danger"
    elif severity.lower() in {"medium"}:
        cls = "pill-warn"
    return f"<span class='status-pill {cls}'>{severity}</span>"


def render_dark_table(df: pd.DataFrame, height: int = 320) -> None:
    if df is None or df.empty:
        st.info("No rows to display.")
        return

    safe_df = df.copy()

    for col in safe_df.columns:
        safe_df[col] = safe_df[col].apply(
            lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (dict, list)) else x
        )

    html = safe_df.to_html(index=False, escape=True)

    st.markdown(
        f"""
        <div class="dark-table-scroll" style="max-height:{height}px;">
            {html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_finding_cards(findings, empty_message: str) -> None:
    if not findings:
        st.success(empty_message)
        return

    for finding in findings[:80]:
        loc = f":{finding.line}" if finding.line else ""
        st.markdown(
            f"""
            <div class='panel'>
                {severity_pill(finding.severity)} <b>{finding.title}</b>
                <br><span class='small-muted'>{finding.category} · {finding.file_path}{loc}</span>
                <div class='evidence'><b>Evidence</b><br>{finding.evidence}</div>
                <span class='small-muted'><b>Recommendation:</b> {finding.recommendation}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def make_graph_dot(edges: list[tuple[str, str, str]], max_edges: int = 80) -> str:
    if not edges:
        return """
        digraph G {
            graph [
                bgcolor="#070707",
                fontcolor="#ffffff",
                label="No internal dependency edges detected",
                labelloc="t",
                fontsize=18,
                pad="0.45"
            ];

            empty [
                label="No internal dependency edges detected",
                shape=box,
                style="rounded,filled",
                fillcolor="#111111",
                color="#2f80ed",
                fontcolor="#ffffff",
                penwidth=2,
                margin="0.18,0.12"
            ];
        }
        """

    dot = [
        "digraph G {",
        'graph [bgcolor="#070707", pad="0.45", ranksep="0.9", nodesep="0.6"];',
        "rankdir=LR;",
        'node [shape=box, style="rounded,filled", fillcolor="#111111", color="#2f80ed", fontcolor="#ffffff", penwidth=2.4, fontsize=12, margin="0.18,0.12"];',
        'edge [color="#ff8a1f", fontcolor="#ffffff", fontsize=10, penwidth=2, arrowsize=0.85];',
    ]

    for src, dst, label in edges[:max_edges]:
        safe_src = src.replace('"', "'")
        safe_dst = dst.replace('"', "'")
        safe_label = label.replace('"', "'")[:32]
        dot.append(f'"{safe_src}" -> "{safe_dst}" [label="{safe_label}"];')

    dot.append("}")
    return "\n".join(dot)


def build_repo_brief(analysis) -> str:
    return json.dumps(
        {
            "repo_name": analysis.repo_name,
            "files_analyzed": len(analysis.files),
            "languages": analysis.language_counts,
            "frameworks": analysis.architecture.get("frameworks_detected"),
            "entrypoints": analysis.architecture.get("entrypoints"),
            "security_findings": [f.title for f in analysis.security_findings[:12]],
            "risk_findings": [f.title for f in analysis.risk_findings[:12]],
            "test_gaps": [f.title for f in analysis.test_gap_findings[:12]],
            "refactor_plan": analysis.refactor_plan,
        },
        indent=2,
    )


client = AIReasoningClient()

with st.sidebar:
    st.markdown("### CodeOps AI")
    st.markdown(
        "<span class='small-muted'>Codebase intelligence workspace for repo analysis, dependency review, risky file detection, security checks, test gap review, PR review, and documentation generation.</span>",
        unsafe_allow_html=True,
    )

    st.divider()

    st.markdown("**Connection status**")

    if client.configured:
        st.success("AI reasoning engine configured")
    else:
        st.warning("Local analysis mode")

    st.caption(client.status_help)

    st.divider()

    max_files = st.slider(
        "Source files to process",
        min_value=50,
        max_value=800,
        value=350,
        step=50,
    )

    use_ai_summary = st.checkbox(
        "Use AI narrative review when configured",
        value=True,
    )

    st.caption(
        "Static analysis always runs first. AI is only used to refine narrative explanations and code Q&A."
    )

    st.divider()

    st.markdown("**Advanced checks included**")
    st.markdown(
        "- Repo architecture map\n"
        "- Internal dependency graph\n"
        "- Risky file detection\n"
        "- Security smell detection\n"
        "- Test gap detection\n"
        "- Generated starter tests\n"
        "- PR diff reviewer\n"
        "- Refactor roadmap\n"
        "- README/documentation draft\n"
        "- Audit JSON + CSV exports"
    )

st.markdown(
    """
    <div class='hero'>
        <div class='hero-title'>CodeOps AI</div>
        <div class='hero-subtitle'>
            Codebase RAG and PR review workspace for teams that need fast repo understanding, architecture mapping,
            risk detection, security review, test planning, refactor guidance, and client-ready documentation without exposing secrets.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("## 1. Upload a repository")

col1, col2 = st.columns([1.2, 1])

with col1:
    uploaded_zip = st.file_uploader(
        "Upload GitHub repo ZIP",
        type=["zip"],
        accept_multiple_files=False,
    )

with col2:
    repo_label = st.text_input(
        "Repository name",
        value="sample-shop-api" if not uploaded_zip else Path(uploaded_zip.name).stem,
    )

    use_sample = st.checkbox(
        "Use included sample repo if no upload",
        value=True,
    )

run_col, _ = st.columns([1, 1.2])

with run_col:
    run_analysis = st.button(
        "Run codebase analysis",
        use_container_width=True,
    )

if "analysis" not in st.session_state:
    st.session_state.analysis = None

if "narrative" not in st.session_state:
    st.session_state.narrative = ""

if "exports" not in st.session_state:
    st.session_state.exports = {}

if run_analysis:
    if uploaded_zip is None and not use_sample:
        st.error("Upload a repo ZIP or keep sample repo enabled.")
    else:
        with st.spinner(
            "Analyzing repository structure, code risks, security smells, tests, and docs..."
        ):
            reset_dir(WORK_DIR)

            zip_path = WORK_DIR / "repo.zip"

            if uploaded_zip is not None:
                write_uploaded_file(uploaded_zip, zip_path)
            else:
                zip_path.write_bytes(SAMPLE_ZIP.read_bytes())

            extract_dir = WORK_DIR / "extracted"
            safe_extract_zip(zip_path, extract_dir)

            repo_root = find_repo_root(extract_dir)

            analysis = analyze_repo(
                repo_root,
                repo_label.strip() or repo_root.name,
                limit_files=max_files,
            )

            if use_ai_summary and client.configured:
                narrative = enrich_summary(client, build_repo_brief(analysis))
            else:
                narrative = (
                    "Static review completed. Configure the local AI reasoning engine "
                    "to generate a narrative senior-engineer summary."
                )

            exports = save_exports(BASE_DIR, analysis, narrative)

            st.session_state.analysis = analysis
            st.session_state.narrative = narrative
            st.session_state.exports = exports

        st.success("Codebase analysis complete.")

analysis = st.session_state.analysis
narrative = st.session_state.narrative
exports = st.session_state.exports

if analysis:
    st.divider()

    st.markdown("## Workflow result")

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        metric_card(
            "Files analyzed",
            str(len(analysis.files)),
            "Filtered source/config/docs",
        )

    with c2:
        metric_card(
            "Security findings",
            str(len(analysis.security_findings)),
            "Human review recommended",
        )

    with c3:
        metric_card(
            "Risk findings",
            str(len(analysis.risk_findings)),
            "Maintainability/reliability",
        )

    with c4:
        metric_card(
            "Test gaps",
            str(len(analysis.test_gap_findings)),
            "Coverage and CI readiness",
        )

    tabs = st.tabs(
        [
            "Overview",
            "Architecture Map",
            "Dependency Graph",
            "Risk + Security",
            "Test Gap + Generated Tests",
            "PR Reviewer",
            "Ask Codebase",
            "Refactor Plan",
            "Docs + Export",
        ]
    )

    with tabs[0]:
        st.markdown("### Senior-engineer summary")
        st.markdown(
            f"<div class='panel'>{narrative}</div>",
            unsafe_allow_html=True,
        )

        a, b = st.columns([1, 1])

        with a:
            st.markdown("### Language mix")

            if analysis.loc_by_language:
                df_lang = (
                    pd.DataFrame(
                        [
                            {"language": k, "loc": v}
                            for k, v in analysis.loc_by_language.items()
                        ]
                    )
                    .sort_values("loc", ascending=False)
                )

                fig = px.bar(
                    df_lang,
                    x="language",
                    y="loc",
                    title="Lines of code by language",
                )

                fig.update_layout(
                    height=360,
                    paper_bgcolor="#070707",
                    plot_bgcolor="#111111",
                    font_color="#ffffff",
                )

                fig.update_xaxes(gridcolor="#242424")
                fig.update_yaxes(gridcolor="#242424")

                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No source files detected.")

        with b:
            st.markdown("### Repository health")

            score = max(
                0,
                100
                - len(analysis.security_findings) * 10
                - len(analysis.risk_findings) * 2
                - len(analysis.test_gap_findings) * 2,
            )

            fig = go.Figure(
                go.Indicator(
                    mode="gauge+number",
                    value=score,
                    number={"suffix": "%"},
                    gauge={
                        "axis": {"range": [0, 100]},
                        "bar": {"thickness": 0.28},
                    },
                )
            )

            fig.update_layout(
                height=360,
                paper_bgcolor="#070707",
                font_color="#ffffff",
                margin=dict(l=10, r=10, t=20, b=10),
            )

            st.plotly_chart(fig, use_container_width=True)

        st.markdown("### File inventory")

        file_df = (
            pd.DataFrame(
                [
                    {
                        "path": f.path,
                        "language": f.language,
                        "loc": f.loc,
                        "functions": len(f.functions),
                        "classes": len(f.classes),
                        "imports": len(f.imports),
                        "complexity": f.complexity,
                        "is_test": f.is_test,
                    }
                    for f in analysis.files
                ]
            )
            .sort_values("loc", ascending=False)
        )

        render_dark_table(file_df, height=360)

    with tabs[1]:
        st.markdown("### Architecture detection")

        arch = analysis.architecture
        chips = arch.get("frameworks_detected") or ["No framework strongly detected"]

        st.markdown(
            " ".join([f"<span class='status-pill'>{c}</span>" for c in chips]),
            unsafe_allow_html=True,
        )

        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("#### Entry points")
            st.write(arch.get("entrypoints") or ["Not clearly detected"])

            st.markdown("#### Config files")
            st.write(arch.get("config_files") or ["Not detected"])

        with col_b:
            st.markdown("#### Dependency files")
            st.write(arch.get("dependency_files") or ["Not detected"])

            st.markdown("#### Documentation files")
            st.write(arch.get("documentation_files") or ["Not detected"])

        st.markdown("#### Notable dependencies")
        st.write(arch.get("notable_dependencies") or ["Not detected"])

    with tabs[2]:
        st.markdown("### Internal dependency graph")
        st.caption(
            "This graph uses local imports detected from Python/JS/TS files. Large external libraries are intentionally excluded."
        )

        if analysis.dependency_edges:
            st.graphviz_chart(
                make_graph_dot(analysis.dependency_edges),
                use_container_width=True,
            )

            edge_df = pd.DataFrame(
                analysis.dependency_edges,
                columns=["source", "target", "import"],
            )

            render_dark_table(edge_df, height=340)
        else:
            st.graphviz_chart(
                make_graph_dot([]),
                use_container_width=True,
            )

            st.info(
                "No internal dependency edges detected. This can happen with small repos or projects that use external packages only."
            )

    with tabs[3]:
        st.markdown("### Security findings")
        show_finding_cards(
            analysis.security_findings,
            "No security smells detected by local scan.",
        )

        st.markdown("### Maintainability and reliability risks")
        show_finding_cards(
            analysis.risk_findings,
            "No maintainability risks detected by local scan.",
        )

    with tabs[4]:
        st.markdown("### Test gap review")
        show_finding_cards(
            analysis.test_gap_findings,
            "No major test gaps detected by local scan.",
        )

        st.markdown("### Generated starter tests")

        test_names = list(analysis.generated_tests.keys())

        if test_names:
            selected_test = st.selectbox("Select generated test file", test_names)

            if selected_test:
                st.code(
                    analysis.generated_tests[selected_test],
                    language="python",
                )
        else:
            st.info("No generated starter tests available.")

    with tabs[5]:
        st.markdown("### Pull request diff reviewer")
        st.caption(
            "Paste a unified diff or upload a .diff/.patch file. The sample diff is intentionally risky so you can see the review behavior."
        )

        diff_upload = st.file_uploader(
            "Upload PR diff",
            type=["diff", "patch", "txt"],
            key="diff_upload",
        )

        default_diff = (
            SAMPLE_DIFF.read_text(encoding="utf-8")
            if SAMPLE_DIFF.exists()
            else ""
        )

        diff_text = st.text_area(
            "Unified diff",
            value=diff_upload.read().decode("utf-8", errors="replace")
            if diff_upload
            else default_diff,
            height=260,
        )

        if st.button("Review PR diff", use_container_width=True):
            pr_review = review_diff(diff_text, client)
            st.session_state.pr_review = pr_review

        pr_review = st.session_state.get("pr_review")

        if pr_review:
            sc1, sc2, sc3, sc4 = st.columns(4)

            with sc1:
                metric_card(
                    "Files changed",
                    str(pr_review.summary["files_changed"]),
                    "Diff scope",
                )

            with sc2:
                metric_card(
                    "Additions",
                    str(pr_review.summary["additions"]),
                    "New lines",
                )

            with sc3:
                metric_card(
                    "Deletions",
                    str(pr_review.summary["deletions"]),
                    "Removed lines",
                )

            with sc4:
                metric_card(
                    "Risk comments",
                    str(pr_review.summary["risk_comments"]),
                    "Review blockers",
                )

            st.markdown("#### PR review summary")
            st.markdown(
                f"<div class='panel'>{pr_review.ai_review}</div>",
                unsafe_allow_html=True,
            )

            st.markdown("#### File-level diff stats")
            render_dark_table(
                pd.DataFrame([f.__dict__ for f in pr_review.files]),
                height=280,
            )

            st.markdown("#### Review comments")
            render_dark_table(
                pd.DataFrame(pr_review.comments),
                height=340,
            )

            st.download_button(
                "Download PR review JSON",
                data=json.dumps(pr_review.to_dict(), indent=2),
                file_name="codeops_pr_review.json",
                mime="application/json",
                use_container_width=True,
            )

    with tabs[6]:
        st.markdown("### Ask the codebase")

        question = st.text_input(
            "Question",
            placeholder="Example: Where is refund logic implemented and what risks exist?",
        )

        if st.button("Ask codebase", use_container_width=True):
            chunks = make_chunks(analysis.files)
            st.session_state.code_answer = answer_code_question(
                client,
                question,
                chunks,
            )

        answer = st.session_state.get("code_answer")

        if answer:
            st.markdown(
                f"<div class='panel'>{answer}</div>",
                unsafe_allow_html=True,
            )

    with tabs[7]:
        st.markdown("### Refactor roadmap")

        for item in analysis.refactor_plan:
            actions = "".join([f"<li>{a}</li>" for a in item.get("actions", [])])

            st.markdown(
                f"""
                <div class='panel'>
                    {severity_pill(item.get('priority', 'P2'))} <b>{item.get('area')}</b>
                    <br><span class='small-muted'>{item.get('why')}</span>
                    <ul>{actions}</ul>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[8]:
        st.markdown("### README draft")
        st.code(analysis.readme_draft, language="markdown")

        st.markdown("### Exports")

        report_text = make_markdown_report(analysis, narrative)

        ex1, ex2, ex3, ex4 = st.columns(4)

        with ex1:
            st.download_button(
                "Download audit JSON",
                data=json.dumps(analysis.to_dict(), indent=2, default=str),
                file_name="codeops_audit.json",
                mime="application/json",
                use_container_width=True,
            )

        with ex2:
            st.download_button(
                "Download report MD",
                data=report_text,
                file_name="codeops_review_report.md",
                mime="text/markdown",
                use_container_width=True,
            )

        with ex3:
            csv_data = findings_to_df(
                analysis.security_findings
                + analysis.risk_findings
                + analysis.test_gap_findings
            ).to_csv(index=False)

            st.download_button(
                "Download findings CSV",
                data=csv_data,
                file_name="codeops_findings.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with ex4:
            if exports.get("tests_zip") and Path(exports["tests_zip"]).exists():
                st.download_button(
                    "Download tests ZIP",
                    data=Path(exports["tests_zip"]).read_bytes(),
                    file_name="generated_tests.zip",
                    mime="application/zip",
                    use_container_width=True,
                )

else:
    st.divider()

    with st.expander("How to test quickly", expanded=True):
        st.markdown(
            """
            1. Keep **Use included sample repo** checked.
            2. Click **Run codebase analysis**.
            3. Open **Risk + Security** to see hardcoded secret, unsafe subprocess, pickle, weak hash, unsafe YAML, SQL string risk, and CORS wildcard.
            4. Open **Test Gap + Generated Tests** to see generated pytest starter files.
            5. Open **PR Reviewer** and click **Review PR diff** using the sample diff.
            6. Open **Ask Codebase** and ask: `Where is refund logic implemented and what risks exist?`
            7. Open **Docs + Export** to download audit JSON, report markdown, findings CSV, and generated tests.
            """
        )