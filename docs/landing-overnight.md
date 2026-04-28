# Landing site overnight log — 2026-04-28

Chronological log of work on `site/` between Mario going to bed and waking
up. Read top-to-bottom. Decisions, deferrals, and anything that needs
your call before launch are flagged in **bold**.

## Context: your four answers

- **Q1 GitHub visibility** → repo stays private for now; will go public when
  more advanced. Implication: CTAs can include the GitHub URL but readers
  can't actually visit it today. I'll write copy that doesn't hinge on
  the link working.
- **Q2 Aspirational features** → leave Huxley-grows + Huxley-market sections
  as-is. You'll build them before launch. So nothing in those sections is
  an "inconsistency" to flag — it's known-future the section already
  represents.
- **Q3 Translations** → all three languages (EN/ES/FR), reviewed by you and
  friends. So I do real i18n with full string extraction and machine-translated
  ES + FR drafts; you and your reviewers polish.
- **Q4 Hosting** → Vercel. I'll add `vercel.json`.

## Plan

1. Status log (this file) ← _here_
2. Fix the orb (currently black; should be white-glow like the PWA)
3. Audit copy vs reality → `docs/landing-audit.md`
4. Patch landing where audit found wrong claims
5. Mobile responsive pass
6. i18n with react-i18next; EN base + ES/FR drafts
7. Depth sections — roadmap, real metrics, what-ships-today
8. `vercel.json`
9. Critic agent — fresh context, reads as a first-time HN visitor
10. Address critic findings
11. Final polish + push

## Log

(filled in as I work)
