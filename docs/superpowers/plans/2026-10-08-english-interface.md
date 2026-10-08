# English Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execute continuously under the user's English-interface and main-integration authorization.

**Goal:** Add a usable bilingual interface and report export, then merge verified upgrade into main and deliver matching packages.

**Architecture:** One dependency-free local browser translation module, explicit translated UI strings, language-aware printable reports. Backend evidence stays authoritative and unchanged.

**Tech Stack:** Existing standard-library Python HTTP/SQLite and plain JavaScript, existing pytest/Node/Playwright.

## Global Constraints

- English/en and Chinese/zh only; HTML export default zh preserves compatibility.
- No new dependency, hardware operation, credential-in-URL or translation of user metadata/evidence.
- Reuse isolated checkout E:/学习/实习/工控验收升级, current feature branch; preserve12existing commits and v3.0.0.
- Runtime/source provenance remains pinned. Publish only exact reviewed/verified source.

### Task1: Bilingual current and legacy interface

Files: new web/assets/i18n.js; web/index.html, web/legacy.html, web/assets/app.js/style.css; new tests/browser_i18n.cjs plus affected existing JavaScript-helper tests. Controller serves /assets/i18n.js via existing allowlist.

Interface: global QLI18N supplies selected language and explicit text lookup; storage key qldc.lang, values en/zh. Export URL adds &lang=selected language. Preserve all existing data actions and test selectors.

- [ ] Add real-browser failing regression: `await page.getByLabel('Interface language').selectOption('en'); await page.getByRole('button',{name:'New inspection',exact:true}).click();` assert English form and unchanged typed station after switching. Cover all pages, task/retest/compare, report, legacy, reload, blocked storage and mobile.
- [ ] Run against current code and capture missing-language-control failure.
- [ ] Implement explicit translations and selector; never replace innerHTML text globally or mutate task/config data. Preserve forms/active polling/comparison selections.
- [ ] Run new browser test and existing Chinese workflow/Node helper tests on owned demo port. No browser/runtime downloads. Commit only owned frontend/tests files; report red/green evidence in this plan's ignored workspace.

### Task2: Language-aware report API and delivery docs

Files: inspection/reports.py/http.py, tests/test_report_languages.py, README.md/README_zh.md, docs/API.md/RELEASE.md/VALIDATION.md, CHANGELOG.md, version references and release smoke/build default.

Interfaces: `export_job(job, format, lang='zh')`; `html_report(job, marker, lang='zh')`; `GET /api/jobs/{id}/export?format=html&lang=en` returns English portable report, invalid lang returns400. Serve exact new i18n asset; no asset-directory widening.

- [ ] Add failing tests: `export_job(job,'html',lang='en')`, English title/scope/verdict and complete escaped raw evidence; default Chinese and JSON/CSV content equality; invalid language HTTP400; exact new asset200/traversal404.
- [ ] Implement explicit report text mappings and validated query forwarding; preserve evidence. Run focused tests then full targeted coverage/self-test/syntax checks.
- [ ] Synchronize bilingual usage docs and minor version3.1.0; keep93existing regressions. Record actual new outcomes. Commit owned files only.

### Task3: Review, verify and integrate main

- [ ] Independent task/final review at exact commit, disposition of actionable findings, affected regressions.
- [ ] Push preserved-SHA branch (GitHub REST fallback if Git transport fails), update attached PR1 with final scope and checks. All4CI matrix jobs must pass.
- [ ] Verify main/base and expected PR head, merge by GitHub connector with SHA guard, verify PR merged and remote main contains final feature commit. Update local tracking without resetting work.
- [ ] Buildv3.1.0 from verified main/source, fresh Windows offline package smoke, manifests/hashes for both platforms. Publish immutable tag/Release under standing authorization and verify remote assets. Keep prior Release intact.
