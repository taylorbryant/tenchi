# Documentation shell parity

Tenchi's documentation shell follows the Beignet docs application in
`~/Developer/beignet/apps/docs`. Shared behavior should feel the same on both
sites even though their content and visual identities differ.

## Shared surface

Treat these files as ports of their Beignet counterparts:

- `components/callout.tsx`
- `components/copy-button.tsx`
- `components/footer.tsx`
- `components/nav.tsx`
- `components/prev-next.tsx`
- `components/search.tsx`
- `components/theme-toggle.tsx`
- `components/toc.tsx`
- `components/use-theme.ts`
- `lib/search.ts`

When Beignet fixes behavior in one of these files, port the fix to Tenchi. If
Tenchi develops a generally useful improvement, apply it to Beignet as well
when possible. The mobile navigation, search dialog, search ranking, theme
controls, table of contents, previous/next navigation, code copying, and
callouts should not diverge accidentally.

## Intentional differences

Tenchi keeps its own:

- name, logo, green color palette, package version, and GitHub/PyPI links
- Python-oriented content, routes, search defaults, and metadata
- static-export and optional base-path support for GitHub Pages
- internal Next.js links, heading scroll offsets, skip link, focus treatment,
  reduced-motion handling, and mobile touch/overscroll safeguards

These differences are deliberate. Other behavior or layout changes should be
explained here or brought back into alignment.

## Review workflow

Before changing the shared shell:

1. Compare the relevant file with `~/Developer/beignet/apps/docs`.
2. Preserve only the intentional differences above.
3. Run `bun run check` from `docs/`.
4. Check search, theme switching, and navigation at desktop and mobile widths.

The docs smoke test protects the mobile search font size, SVG theme controls,
and overscroll containment that have regressed during earlier ports.
