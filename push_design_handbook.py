#!/usr/bin/env python3
"""
Create Skogum-R-D/design-handbook repo and push all MkDocs files via GitHub API.
Uses only stdlib (urllib, json, base64).
"""

import json
import base64
import urllib.request
import urllib.error
import sys

import os
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
ORG = "Skogum-R-D"
REPO = "design-handbook"
BRANCH = "main"
COMMIT_MESSAGE = "docs: initial design handbook"

API_BASE = "https://api.github.com"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
    "User-Agent": "skogum-push-script/1.0",
}


def api_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"HTTP {e.code} on {method} {url}")
        print(err_body)
        raise


def b64(content: str) -> str:
    return base64.b64encode(content.encode()).decode()


# ---------------------------------------------------------------------------
# File contents
# ---------------------------------------------------------------------------

FILES: dict[str, str] = {}

FILES["mkdocs.yml"] = """\
site_name: Skogum R&D Design Handbook
site_description: Frontend design system, component patterns, and Next.js gotchas for the Skogum agent team
repo_url: https://github.com/Skogum-R-D/design-handbook
theme:
  name: material
  palette:
    scheme: slate
    primary: indigo
    accent: deep purple
  features:
    - navigation.tabs
    - navigation.sections
    - content.code.copy
    - search.highlight

nav:
  - Home: index.md
  - Stack & Versions: stack.md
  - Design System:
    - Colors & Themes: design/colors.md
    - Typography: design/typography.md
    - Spacing & Layout: design/layout.md
    - Glassmorphism: design/glassmorphism.md
    - Animations: design/animations.md
  - Components:
    - Button: components/button.md
    - Card: components/card.md
    - Input & Textarea: components/input.md
  - Gotchas & Fixes: gotchas.md

markdown_extensions:
  - admonition
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.superfences
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.details
  - attr_list

extra:
  social:
    - icon: fontawesome/brands/github
      link: https://github.com/Skogum-R-D
"""

FILES["requirements.txt"] = """\
mkdocs>=1.6.0
mkdocs-material>=9.5.0
"""

FILES["docs/index.md"] = """\
# Skogum R&D Design Handbook

This handbook is the single source of truth for all frontend work at Skogum R&D. The frontend agent reads these docs before generating any code.

## What's in here

- **Stack & Versions** — exact pinned versions of every package. Never deviate.
- **Design System** — colors, typography, spacing, glassmorphism, animations
- **Components** — working reference implementations for Button, Card, Input
- **Gotchas & Fixes** — every bug we've hit and how to fix it. Read this before debugging.

## Core principles

- Dark theme by default — `class="dark"` on `<html>`
- Glassmorphism cards with `backdrop-filter: blur`
- Framer Motion for all animations — entrance, hover, stagger
- Mobile-first responsive layout
- No placeholder images — use CSS gradients or SVG
- Every component that uses framer-motion must have `"use client"` at the top
"""

FILES["docs/stack.md"] = """\
# Stack & Versions

Always use these exact versions. Do not upgrade without updating this doc.

## Core

| Package | Version | Notes |
|---------|---------|-------|
| `next` | `16.2.0` | App Router, Turbopack dev server |
| `react` | `^19.0.0` | **Not** the RC string `^19.0.0-rc-...` |
| `react-dom` | `^19.0.0` | Must match react |
| `typescript` | `^5.4.5` | |

## Styling

| Package | Version | Notes |
|---------|---------|-------|
| `tailwindcss` | `^3.4.0` | **v3 only** — v4 uses different CSS syntax, breaks everything |
| `autoprefixer` | `^10.4.19` | |
| `postcss` | `^8.4.38` | |
| `tailwindcss-animate` | `^1.0.7` | |
| `tailwind-merge` | `^2.3.0` | |
| `class-variance-authority` | `^0.7.0` | |
| `clsx` | `^2.1.1` | |

## Animation

| Package | Version | Notes |
|---------|---------|-------|
| `framer-motion` | `^11.3.28` | All components using this need `"use client"` |

## UI Primitives

| Package | Version | Notes |
|---------|---------|-------|
| `lucide-react` | `^0.468.0` | Must be >=0.468.0 for React 19 support |
| `@radix-ui/react-slot` | `^1.1.0` | |
| `next-themes` | `^0.3.0` | **Do not use** with React 19 — see Gotchas |

## package.json template

```json
{
  "dependencies": {
    "next": "16.2.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "tailwindcss": "^3.4.0",
    "framer-motion": "^11.3.28",
    "lucide-react": "^0.468.0",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.1",
    "tailwind-merge": "^2.3.0",
    "tailwindcss-animate": "^1.0.7"
  },
  "devDependencies": {
    "@types/node": "^20.14.2",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.38",
    "typescript": "^5.4.5"
  }
}
```

## postcss.config.mjs

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

!!! warning
    Never use `@tailwindcss/postcss` — that is the Tailwind v4 plugin and is incompatible with v3 syntax.
"""

FILES["docs/design/colors.md"] = """\
# Colors & Themes

## CSS Variables (globals.css)

All colors are defined as HSL channel values (no `hsl()` wrapper) so Tailwind can apply opacity modifiers.

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 222 47% 5%;
    --foreground: 210 40% 98%;
    --card: 222 47% 8%;
    --card-foreground: 210 40% 98%;
    --popover: 222 47% 5%;
    --popover-foreground: 210 40% 98%;
    --primary: 210 100% 56%;
    --primary-foreground: 222 47% 5%;
    --secondary: 217 33% 17%;
    --secondary-foreground: 210 40% 98%;
    --muted: 217 33% 17%;
    --muted-foreground: 215 20% 65%;
    --accent: 250 80% 65%;
    --accent-foreground: 210 40% 98%;
    --destructive: 0 84% 60%;
    --destructive-foreground: 210 40% 98%;
    --border: 217 33% 20%;
    --input: 217 33% 17%;
    --ring: 210 100% 56%;
    --radius: 0.75rem;
  }
}

@layer utilities {
  .glassmorphism {
    background: rgba(15, 23, 42, 0.6);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.08);
  }

  .gradient-text {
    background: linear-gradient(135deg, #3b82f6, #8b5cf6, #ec4899);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }

  .gradient-bg {
    background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 50%, #ec4899 100%);
  }
}

* { border-color: hsl(var(--border)); }
body {
  background-color: hsl(var(--background));
  color: hsl(var(--foreground));
}
```

## tailwind.config.ts

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
};

export default config;
```

## Brand Gradient

Blue → Purple → Pink: `linear-gradient(135deg, #3b82f6, #8b5cf6, #ec4899)`

Use `.gradient-text` for headings, `.gradient-bg` for buttons and accents.

## Dark mode setup

Set `class="dark"` directly on `<html>` — do **not** rely on `next-themes` (React 19 incompatible):

```tsx
<html lang="en" className="dark" suppressHydrationWarning>
```
"""

FILES["docs/design/typography.md"] = """\
# Typography

## Font

Use Geist or Inter via `next/font/google`:

```tsx
import { Inter } from "next/font/google";
const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
```

Apply on body: `className={inter.variable + " font-sans antialiased"}`.

## Scale

| Use | Class | Size |
|-----|-------|------|
| Hero headline | `text-5xl md:text-7xl font-bold tracking-tight` | 48–72px |
| Section heading | `text-3xl md:text-4xl font-bold` | 30–36px |
| Card title | `text-xl font-semibold` | 20px |
| Body | `text-base` | 16px |
| Muted / caption | `text-sm text-muted-foreground` | 14px |

## Gradient headings

```tsx
<h1 className="gradient-text text-6xl font-bold tracking-tight">
  AI Solutions for the Future
</h1>
```

Always pair `gradient-text` with an explicit font-size and `font-bold`.
"""

FILES["docs/design/layout.md"] = """\
# Spacing & Layout

## Page structure

```tsx
<main className="min-h-screen">
  <Hero />
  <Services />
  <About />
  <Team />
  <Contact />
</main>
```

## Section wrapper

```tsx
<section className="py-20 px-4">
  <div className="container mx-auto max-w-6xl">
    {/* content */}
  </div>
</section>
```

## Grid patterns

| Pattern | Class |
|---------|-------|
| 4-column features | `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6` |
| 3-column cards | `grid grid-cols-1 md:grid-cols-3 gap-8` |
| 2-column split | `grid grid-cols-1 lg:grid-cols-2 gap-12 items-center` |

## Spacing scale

- Section padding: `py-20` (80px)
- Between heading and content: `mb-12`
- Card internal: `p-6`
- Stack of items: `space-y-4` or `gap-6`
"""

FILES["docs/design/glassmorphism.md"] = """\
# Glassmorphism

The `.glassmorphism` utility class is defined in `globals.css`:

```css
.glassmorphism {
  background: rgba(15, 23, 42, 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.08);
}
```

## Usage

Apply to any card or panel container:

```tsx
<div className="glassmorphism rounded-xl p-6">
  {/* content */}
</div>
```

## Hover effect

Use Framer Motion's `whileHover` for a lift effect:

```tsx
<motion.div
  className="glassmorphism rounded-xl p-6"
  whileHover={{ y: -4, boxShadow: "0 20px 40px -10px rgba(59,130,246,0.15)" }}
  transition={{ type: "spring", stiffness: 300, damping: 20 }}
>
```

## Rules

- Always pair with a dark background — glassmorphism needs depth behind it
- Use `rounded-xl` (12px) as the standard border radius
- Keep `border-opacity` low (0.08–0.12) — subtle is better
- Do not stack multiple glassmorphism layers
"""

FILES["docs/design/animations.md"] = """\
# Animations

All animations use Framer Motion. Every file that imports from `framer-motion` **must** have `"use client"` as the first line.

## Standard entrance

```tsx
"use client";
import { motion } from "framer-motion";

<motion.div
  initial={{ opacity: 0, y: 20 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ duration: 0.5 }}
>
```

## Staggered children

```tsx
const container = {
  hidden: {},
  show: { transition: { staggerChildren: 0.1 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5 } },
};

<motion.ul variants={container} initial="hidden" animate="show">
  {items.map((i) => (
    <motion.li key={i.id} variants={item}>{i.content}</motion.li>
  ))}
</motion.ul>
```

## Hover lift (cards)

```tsx
whileHover={{ y: -4, boxShadow: "0 20px 40px -10px rgba(59,130,246,0.15)" }}
transition={{ type: "spring", stiffness: 300, damping: 20 }}
```

## Button press

```tsx
whileHover={{ scale: 1.05 }}
whileTap={{ scale: 0.95 }}
transition={{ type: "spring", stiffness: 400, damping: 17 }}
```

## Timing conventions

| Type | Duration |
|------|----------|
| Fade in | 0.5s |
| Slide up | 0.5s |
| Stagger delay | 0.1s between items |
| Hover spring | stiffness 300, damping 20 |
| Tap spring | stiffness 400, damping 17 |
"""

FILES["docs/components/button.md"] = '''\
# Button

```tsx
"use client";
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { motion } from "framer-motion";

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none ring-offset-background",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        outline: "border border-input hover:bg-accent hover:text-accent-foreground",
        ghost: "hover:bg-accent hover:text-accent-foreground",
      },
      size: {
        default: "h-10 py-2 px-4",
        sm: "h-9 px-3 rounded-md",
        lg: "h-11 px-8 rounded-md",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant, size, ...props }, ref) => (
    <motion.button
      ref={ref}
      className={buttonVariants({ variant, size, className })}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      transition={{ type: "spring", stiffness: 400, damping: 17 }}
      {...props}
    />
  )
);

Button.displayName = "Button";
export { Button, buttonVariants };
```

## Usage

```tsx
<Button>Get Started</Button>
<Button variant="outline" size="lg">Learn More</Button>
```

!!! warning
    Always default `className` to `""` in `forwardRef` components. If it defaults to `undefined`, it renders literally as the string `"undefined"` in the class list.
'''

FILES["docs/components/card.md"] = '''\
# Card

```tsx
"use client";
import * as React from "react";
import { motion } from "framer-motion";

const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className = "", ...props }, ref) => (
    <motion.div
      ref={ref}
      className={`glassmorphism rounded-xl ${className}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      whileHover={{ y: -4, boxShadow: "0 20px 40px -10px rgba(59,130,246,0.15)" }}
      {...props}
    />
  )
);

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className = "", ...props }, ref) => (
    <div ref={ref} className={`flex flex-col space-y-1.5 p-6 ${className}`} {...props} />
  )
);

const CardTitle = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className = "", ...props }, ref) => (
    <h3 ref={ref} className={`text-xl font-semibold gradient-text ${className}`} {...props} />
  )
);

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className = "", ...props }, ref) => (
    <div ref={ref} className={`p-6 pt-0 ${className}`} {...props} />
  )
);

Card.displayName = "Card";
CardHeader.displayName = "CardHeader";
CardTitle.displayName = "CardTitle";
CardContent.displayName = "CardContent";

export { Card, CardHeader, CardTitle, CardContent };
```

## Usage

```tsx
<Card>
  <CardHeader>
    <CardTitle>AI Strategy</CardTitle>
  </CardHeader>
  <CardContent>
    <p className="text-muted-foreground">Description here.</p>
  </CardContent>
</Card>
```
'''

FILES["docs/components/input.md"] = '''\
# Input & Textarea

```tsx
import * as React from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className = "", type, ...props }, ref) => (
    <input
      type={type}
      className={`flex h-10 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      ref={ref}
      {...props}
    />
  )
);

Input.displayName = "Input";
export { Input };
```

```tsx
import * as React from "react";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className = "", ...props }, ref) => (
    <textarea
      className={`flex min-h-[80px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      ref={ref}
      {...props}
    />
  )
);

Textarea.displayName = "Textarea";
export { Textarea };
```
'''

FILES["docs/gotchas.md"] = '''\
# Gotchas & Fixes

Every bug we\'ve hit in production. Read this before debugging.

---

## 1. Tailwind v4 breaks everything — always use v3

**Symptom**: CSS not applied, `@tailwind base` directive throws PostCSS errors, `bg-background` class does nothing.

**Cause**: The generated code uses Tailwind v3 syntax. Tailwind v4 uses a completely different CSS import system (`@import "tailwindcss"`) and CSS-first config.

**Fix**: Pin to Tailwind v3 and use the standard PostCSS config:

```json
"tailwindcss": "^3.4.0"
```

```js
// postcss.config.mjs
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
```

Never use `@tailwindcss/postcss` — that is the v4 plugin.

---

## 2. All framer-motion components need "use client"

**Symptom**: `Element type is invalid: expected a string... but got: undefined`

**Cause**: Next.js App Router renders components on the server by default. `framer-motion` is a client-only library. Without `"use client"`, `motion` is `undefined` at runtime.

**Fix**: Add `"use client"` as the **first line** of every file that imports from `framer-motion`:

```tsx
"use client";
import { motion } from "framer-motion";
```

This applies to: page sections, card components, button components — anything that animates.

---

## 3. next-themes is incompatible with React 19

**Symptom**: `Element type is invalid... but got: undefined` even after adding `"use client"` everywhere.

**Cause**: `next-themes@0.3.0` doesn\'t support React 19\'s new rendering model.

**Fix**: Replace the ThemeProvider with a passthrough and set dark mode via the `class` attribute directly on `<html>`:

```tsx
// components/theme-provider.tsx
"use client";
import * as React from "react";

interface ThemeProviderProps {
  children: React.ReactNode;
  [key: string]: unknown;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  return <>{children}</>;
}
```

```tsx
// app/layout.tsx
<html lang="en" className="dark" suppressHydrationWarning>
```

---

## 4. Turbopack is case-sensitive — filenames must match imports exactly

**Symptom**: `Module not found: Can\'t resolve \'@/components/Hero\'` even though `hero.tsx` exists.

**Cause**: Turbopack enforces case-sensitive path resolution even on macOS (which has a case-insensitive filesystem by default).

**Fix**: Ensure the filename on disk matches the import exactly:

```tsx
// Wrong:
import Hero from "@/components/Hero";  // file is hero.tsx

// Correct:
import Hero from "@/components/hero";  // matches hero.tsx
```

Or rename the file to `Hero.tsx` to match the import. Pick one convention and stick to it. We use **PascalCase filenames** for components.

---

## 5. Always use export default for page components

**Symptom**: `Export default doesn\'t exist in target module`

**Cause**: A component uses a named export (`export function Hero()`) but is imported as a default import (`import Hero from ...`).

**Fix**: Always use `export default` for top-level page components:

```tsx
// Wrong:
export function Hero() { ... }

// Correct:
export default function Hero() { ... }
```

Named exports are fine for utility components exported alongside others (e.g. `Card`, `CardHeader`).

---

## 6. Use react@^19.0.0 not the RC version string

**Symptom**: Peer dependency conflicts on `npm install`, especially with `lucide-react`.

**Cause**: The RC version string `^19.0.0-rc-f994737d14-20240522` doesn\'t satisfy `peer react: "^19.0.0"` in some packages.

**Fix**:

```json
"react": "^19.0.0",
"react-dom": "^19.0.0"
```

---

## 7. lucide-react must be >= 0.468.0 for React 19

**Symptom**: `ERESOLVE unable to resolve dependency tree` on `npm install`.

**Cause**: `lucide-react@0.378.0` declares `peer react: "^16.5.1 || ^17.0.0 || ^18.0.0"` — no React 19.

**Fix**:

```json
"lucide-react": "^0.468.0"
```

---

## 8. className must default to "" in forwardRef components

**Symptom**: Class list contains the literal string `"undefined"`, e.g. `class="p-6 undefined"`.

**Cause**: When `className` prop is not passed, it\'s `undefined`. Concatenating `"prefix " + undefined` gives `"prefix undefined"`.

**Fix**: Always destructure with a default:

```tsx
// Wrong:
({ className, ...props }, ref) => (
  <div className={"p-6 " + className} ...>

// Correct:
({ className = "", ...props }, ref) => (
  <div className={`p-6 ${className}`} ...>
```

---

## 9. Import paths must use @/ alias, not absolute /

**Symptom**: `Module not found: Can\'t resolve \'./components/theme-provider\'` or similar.

**Cause**: An import uses an absolute path starting with `/` instead of the `@/` alias.

**Fix**:

```tsx
// Wrong:
import { ThemeProvider } from "/components/theme-provider";

// Correct:
import { ThemeProvider } from "@/components/theme-provider";
```

The `@/` alias is configured in `tsconfig.json` as `"paths": { "@/*": ["./*"] }`.

---

## 10. Dark theme CSS variables — foreground must be light

**Symptom**: Dark background but black text — invisible.

**Cause**: The `--foreground` variable was set to a near-black value (`222.2 84% 4.9%`) in both `:root` and `.dark`, making text invisible on dark backgrounds.

**Fix**: `--foreground` must be a **light** value in dark mode:

```css
:root {
  --background: 222 47% 5%;   /* very dark */
  --foreground: 210 40% 98%;  /* very light */
}
```

See [Colors & Themes](design/colors.md) for the full correct variable set.
'''


# ---------------------------------------------------------------------------
# Step 1: Create the repository
# ---------------------------------------------------------------------------

def create_repo():
    print(f"Creating repo {ORG}/{REPO} ...")
    body = {
        "name": REPO,
        "description": "Skogum R&D frontend design system, component patterns, and Next.js gotchas",
        "private": False,
        "auto_init": False,
    }
    try:
        result = api_request("POST", f"/orgs/{ORG}/repos", body)
        print(f"  Repo created: {result['html_url']}")
        return result
    except urllib.error.HTTPError as e:
        # 422 = already exists; that's fine
        if hasattr(e, 'code') and e.code == 422:
            print("  Repo already exists, continuing.")
            return None
        raise


# ---------------------------------------------------------------------------
# Step 2: Push files via Contents API
# ---------------------------------------------------------------------------

def push_file(path: str, content: str):
    print(f"  Pushing {path} ...")
    body = {
        "message": COMMIT_MESSAGE,
        "content": b64(content),
        "branch": BRANCH,
    }

    # Check if file already exists (to get its SHA for updates)
    try:
        existing = api_request("GET", f"/repos/{ORG}/{REPO}/contents/{path}")
        body["sha"] = existing["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # 404 = new file, no sha needed

    result = api_request("PUT", f"/repos/{ORG}/{REPO}/contents/{path}", body)
    print(f"    -> committed {result['content']['sha'][:7]}")


def initialize_main_branch():
    """
    GitHub repos created with auto_init=False have no commits and no default branch.
    A GET on the ref returns 409 (Conflict) when the repo is empty.
    The first PUT to /contents/ will create the branch automatically.
    """
    try:
        api_request("GET", f"/repos/{ORG}/{REPO}/git/ref/heads/{BRANCH}")
        print(f"  Branch '{BRANCH}' already exists.")
    except urllib.error.HTTPError as e:
        if e.code in (404, 409):
            # 404 = branch missing on non-empty repo
            # 409 = repo is completely empty — first file push will create it
            print(f"  Branch '{BRANCH}' not yet initialised (HTTP {e.code}) — first file push will create it.")
        else:
            raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    create_repo()

    # Small pause to let GitHub provision the repo
    import time
    time.sleep(2)

    initialize_main_branch()

    print(f"\nPushing {len(FILES)} files ...")
    for path, content in FILES.items():
        push_file(path, content)

    print(f"\nDone! Repo URL: https://github.com/{ORG}/{REPO}")


if __name__ == "__main__":
    main()
