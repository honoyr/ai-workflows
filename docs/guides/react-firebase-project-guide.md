# React + Firebase Project Build Guide

Lessons learned from building `health-dashboard` and referencing `fire-calculator`.

## 1. Scaffolding Order

Always follow this sequence - later phases depend on earlier ones:

1. **Scaffold** - `npm create vite@latest <name> -- --template react-ts`
2. **Install deps** - Split into app deps and dev deps
3. **Config files** - vite.config.ts, tsconfig, tailwind, firebase.json
4. **Design system** - CSS variables, fonts, theme classes
5. **Types** - Data models first, everything else references them
6. **State layer** - RxJS BehaviorSubjects (auth$, data$, network$)
7. **Hooks** - Thin wrappers subscribing to BehaviorSubjects
8. **Services** - Firebase CRUD, auth, seed data
9. **UI components** - Bottom-up: atoms → molecules → organisms
10. **Pages & routing** - Compose components into pages
11. **App & main** - Wire router, import side-effect state modules
12. **Tests** - Unit (Vitest) + E2E (Playwright)
13. **Verify** - `tsc --noEmit` + `vite build` + `vitest run`

## 2. Directory Structure Convention

```
src/
├── main.tsx                 # Entry point, imports styles
├── App.tsx                  # Router provider + side-effect imports
├── firebase.ts              # Firebase init + emulator connection
├── router.tsx               # createBrowserRouter config
├── types/                   # TypeScript interfaces
├── state/                   # RxJS BehaviorSubjects (auth$, data$)
├── hooks/                   # React hooks wrapping observables
├── services/                # Firebase CRUD, auth, seed data
├── layouts/                 # Shell layouts with <Outlet />
├── pages/                   # Route-level page components
├── components/
│   ├── auth/                # Auth-related (LoginModal)
│   ├── [domain]/            # Domain components (lab/, charts/)
│   └── ui/                  # Shared UI atoms (Spinner, Banner)
├── styles/
│   └── index.css            # Tailwind directives + CSS vars + fonts
└── test/
    └── setup.ts             # @testing-library/jest-dom import
e2e/                         # Playwright specs (outside src/)
```

## 3. Dependency Installation

Split installs to keep things clear:

```bash
# App dependencies
npm install react-router-dom firebase rxjs recharts framer-motion react-day-picker date-fns

# Dev dependencies
npm install -D tailwindcss @tailwindcss/vite @playwright/test vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event @vitest/coverage-v8 jsdom
```

## 4. Vite Config with Tailwind 4

Tailwind CSS 4 uses `@tailwindcss/vite` plugin (NOT postcss). Key gotcha: **must exclude `e2e/` from Vitest** or Playwright specs get picked up as Vitest tests.

```ts
/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    exclude: ['node_modules', 'dist', 'e2e'],  // <-- CRITICAL
  },
} as any)
```

## 5. Tailwind CSS 4 Setup

Uses `@import "tailwindcss"` instead of `@tailwind` directives. CSS custom properties go in `@theme {}` block. Google Fonts `@import url(...)` must come BEFORE `@import "tailwindcss"` to avoid build warnings.

```css
@import url('https://fonts.googleapis.com/css2?family=...');
@import "tailwindcss";

@theme {
  --color-bg-primary: #0a0a1a;
  --font-heading: 'Outfit', system-ui, sans-serif;
}
```

Reference vars in Tailwind classes: `text-[var(--color-text-primary)]` or `bg-[var(--color-bg-primary)]`.

## 6. Firebase Pattern

### firebase.ts
Hardcode config (it's public anyway). Conditionally connect emulators in dev:

```ts
if (import.meta.env.DEV) {
  connectAuthEmulator(auth, "http://127.0.0.1:9099");
  connectFirestoreEmulator(db, "127.0.0.1", 8080);
}
```

### firebase.json (for emulators)
Must create this before running `firebase emulators:start`:

```json
{
  "firestore": { "rules": "firestore.rules" },
  "emulators": {
    "auth": { "port": 9099 },
    "firestore": { "port": 8080 },
    "ui": { "enabled": true, "port": 4000 }
  }
}
```

### Firestore rules (user-scoped)
```
match /users/{userId}/{document=**} {
  allow read, write: if request.auth != null && request.auth.uid == userId;
}
```

## 7. RxJS State Management Pattern

### State modules (side-effect imports)
Each state file creates BehaviorSubjects and sets up subscriptions at module level. Import them in App.tsx as side effects:

```ts
// App.tsx
import "./state/auth$";  // Starts onAuthStateChanged listener
```

### auth$.ts pattern
```ts
export const authUser$ = new BehaviorSubject<User | null>(null);
export const authLoading$ = new BehaviorSubject<boolean>(true);

// Observable wrapping Firebase listener
const authState$ = new Observable<User | null>((subscriber) => {
  const unsubscribe = onAuthStateChanged(auth, (user) => subscriber.next(user));
  return unsubscribe;
}).pipe(shareReplay(1));

authState$.subscribe((user) => {
  authUser$.next(user);
  authLoading$.next(false);
});
```

### Data collection with onSnapshot
Use `distinctUntilChanged` on uid to avoid re-subscribing. Clean up previous Firestore listener when user changes:

```ts
let unsubFirestore: (() => void) | null = null;
authUser$.pipe(distinctUntilChanged((a, b) => a?.uid === b?.uid)).subscribe((user) => {
  if (unsubFirestore) { unsubFirestore(); unsubFirestore = null; }
  if (!user) { data$.next([]); return; }
  unsubFirestore = onSnapshot(query(...), (snapshot) => { ... });
});
```

### Hook pattern (thin wrapper)
```ts
export function useAuth() {
  const [user, setUser] = useState(authUser$.getValue());
  const [loading, setLoading] = useState(authLoading$.getValue());
  useEffect(() => {
    const s1 = authUser$.subscribe(setUser);
    const s2 = authLoading$.subscribe(setLoading);
    return () => { s1.unsubscribe(); s2.unsubscribe(); };
  }, []);
  return { user, loading };
}
```

## 8. Firestore CRUD Pattern

Use `setDoc` with `{ merge: true }` for partial updates (upserts):

```ts
await setDoc(ref, { ...partialData, updatedAt: Timestamp.now() }, { merge: true });
```

For arrays (like results), read current state from the BehaviorSubject, mutate locally, then write the full array back.

## 9. Seed Data Service

Create realistic mock data with:
- Multiple categories with 4-6 tests each
- 6-12 historical data points per test spanning ~2 years
- Intentionally include some out-of-range values for visual variety
- Use `Promise.all` for parallel writes
- Generate dates with randomized intervals

## 10. Playwright Config

Match fire-calculator pattern. Key: separate `testDir: './e2e'` from Vitest's src-based tests. Include mobile viewports.

## 11. Common Gotchas

| Issue | Solution |
|-------|----------|
| Vitest picks up Playwright e2e specs | Add `exclude: ['node_modules', 'dist', 'e2e']` to vitest config |
| CSS `@import url()` warning | Put Google Fonts import BEFORE `@import "tailwindcss"` |
| `verbatimModuleSyntax` in tsconfig.app.json | Newer Vite scaffolds include this; compatible with `import type` |
| Firebase emulators need firebase.json | Create it with emulator port config before running `firebase emulators:start` |
| `rm -f` may be denied in sandbox | Use `Write` to overwrite files instead of deleting them |
| Creating files in new dirs | Must `mkdir -p` the directory first via Bash |
| Vite scaffold doesn't init git | Must `git init` manually before committing |

## 12. Verification Checklist

Before committing, always run:
1. `npx tsc -b --noEmit` - TypeScript compiles cleanly
2. `npx vite build` - Production build succeeds
3. `npx vitest run` - All unit tests pass
4. Manual check: `npm run dev` + open browser

## 13. Git + GitHub Workflow

```bash
git -C /path/to/project init
git -C /path/to/project add [specific files]
git -C /path/to/project commit -m "feat: initial implementation"
gh repo create <name> --private --source=/path/to/project --push
```
