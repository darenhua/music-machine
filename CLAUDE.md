# CLAUDE.md

## Project Overview

Music Machine is a React single-page application built with TypeScript, Vite, and shadcn/ui. It uses TailwindCSS 4 for styling with an oklch color system and supports dark/light theme toggling.

## Tech Stack

- **Framework:** React 19 + TypeScript 5.9
- **Build Tool:** Vite 7
- **Package Manager:** Bun (uses `bun.lock`)
- **Styling:** TailwindCSS 4 with CSS variables (oklch color space)
- **Component Library:** shadcn/ui (radix-nova style) with Radix UI primitives
- **Icons:** HugeIcons (`@hugeicons/react`)
- **Fonts:** Inter Variable (body/sans), Geist Variable (headings)

## Commands

```bash
bun run dev        # Start dev server
bun run build      # TypeScript check + production build
bun run lint       # ESLint
bun run format     # Prettier (writes changes)
bun run typecheck  # TypeScript type checking only (no emit)
bun run preview    # Preview production build
```

## Project Structure

```
src/
├── main.tsx                    # Entry point — renders App inside ThemeProvider
├── App.tsx                     # Root application component
├── index.css                   # Global styles, theme CSS variables, Tailwind imports
├── assets/                     # Static assets (images, SVGs)
├── components/
│   ├── theme-provider.tsx      # ThemeProvider context + useTheme hook
│   └── ui/                     # shadcn/ui components (added via `bunx shadcn add`)
│       └── button.tsx
├── hooks/                      # Custom React hooks (alias: @/hooks)
└── lib/
    └── utils.ts                # Utility functions — cn() for className merging
```

## Path Aliases

`@/*` maps to `./src/*` — configured in both `tsconfig.json` and `vite.config.ts`.

```tsx
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
```

## Code Style

- **No semicolons** — enforced by Prettier
- **Double quotes** for strings
- **2-space indentation**
- **Trailing commas** in ES5 positions
- **80-character line width**
- Prettier auto-sorts TailwindCSS classes via `prettier-plugin-tailwindcss`
- ESLint uses flat config format (ESLint 9) with TypeScript and React plugins

## Adding UI Components

Use the shadcn CLI to add new components:

```bash
bunx shadcn@latest add <component-name>
```

Components are placed in `src/components/ui/`. The configuration in `components.json` sets:
- Style: `radix-nova`
- Icon library: `hugeicons`
- No React Server Components (`rsc: false`)
- CSS variables enabled with oklch colors

## Styling Conventions

- Use TailwindCSS utility classes for all styling
- Use `cn()` from `@/lib/utils` to merge conditional classNames
- Use `cva()` (class-variance-authority) for component variant definitions
- Theme colors are CSS variables defined in `src/index.css` (light in `:root`, dark in `.dark`)
- Dark mode uses the class strategy: `@custom-variant dark (&:is(.dark *))`

## Theme System

- `ThemeProvider` wraps the app in `main.tsx`
- Supports "light", "dark", and "system" themes
- Persisted to `localStorage` under the key `"theme"`
- Press `d` key to toggle dark/light mode (ignored in editable inputs)
- Access via `useTheme()` hook: `const { theme, setTheme } = useTheme()`

## TypeScript

- Strict mode enabled (`strict: true`)
- No unused locals or parameters allowed
- Target: ES2022
- Module resolution: bundler
- JSX: react-jsx (automatic runtime)
