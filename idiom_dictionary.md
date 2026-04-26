# Idiom Dictionary — React→Vue 3

Patterns for converting Claude Design React artifacts to Vue 3 SFCs.
Sections are keyed exactly to idiom names used in the conversion manifest.

---

## className_to_class

```jsx
// React
<div className="sidebar active">
<div className={`btn ${isActive ? 'active' : ''}`}>
<div className={styles.card}>
```
```vue
<!-- Vue -->
<div class="sidebar active">
<div :class="`btn ${isActive ? 'active' : ''}`">
<!-- Or preferably with object syntax: -->
<div :class="{ btn: true, active: isActive }">
<!-- CSS Modules: use $style -->
<div :class="$style.card">
```

---

## style_binding

```jsx
// React
<div style={{ color: 'red', fontSize: 14 }}>
<div style={{ color: textColor, width: `${size}px` }}>
```
```vue
<!-- Vue -->
<div :style="{ color: 'red', fontSize: '14px' }">
<div :style="{ color: textColor, width: `${size}px` }">
```

Note: Vue's `:style` accepts the same camelCase keys as React's `style` prop.
Vue auto-appends `px` to unitless numbers for pixel properties.

---

## event_handlers

Full React→Vue event name map:

| React | Vue |
|-------|-----|
| `onClick` | `@click` |
| `onChange` | `@change` / `@input` (for inputs use `@input`) |
| `onInput` | `@input` |
| `onSubmit` | `@submit` |
| `onKeyDown` | `@keydown` |
| `onKeyUp` | `@keyup` |
| `onKeyPress` | `@keypress` |
| `onMouseEnter` | `@mouseenter` |
| `onMouseLeave` | `@mouseleave` |
| `onMouseMove` | `@mousemove` |
| `onMouseDown` | `@mousedown` |
| `onMouseUp` | `@mouseup` |
| `onFocus` | `@focus` |
| `onBlur` | `@blur` |
| `onScroll` | `@scroll` |
| `onWheel` | `@wheel` |
| `onDrop` | `@drop` |
| `onDragOver` | `@dragover` |
| `onPointerDown` | `@pointerdown` |
| `onPointerUp` | `@pointerup` |
| `onPointerMove` | `@pointermove` |

```jsx
// React
<button onClick={handleClick}>
<button onClick={(e) => doThing(e)}>
<input onChange={(e) => setValue(e.target.value)}>
<form onSubmit={(e) => { e.preventDefault(); submit(); }}>
```
```vue
<!-- Vue -->
<button @click="handleClick">
<button @click="(e) => doThing(e)">
<input @input="(e) => setValue((e.target as HTMLInputElement).value)">
<form @submit.prevent="submit">
```

---

## conditional_rendering

```jsx
// React — short-circuit
{isOpen && <Panel />}

// React — ternary
{isOpen ? <Panel /> : <Placeholder />}

// React — if/else via function or variable
const content = condition ? <A /> : <B />
```
```vue
<!-- Vue — v-if -->
<Panel v-if="isOpen" />

<!-- Vue — v-if / v-else -->
<Panel v-if="isOpen" />
<Placeholder v-else />

<!-- Vue — v-show (keeps DOM, just hides) -->
<Panel v-show="isOpen" />
```

Use `v-show` when the element toggles frequently; `v-if` when it's rarely shown.

---

## list_rendering

```jsx
// React
{items.map((item) => (
  <Row key={item.id} data={item} />
))}

{items.map((item, index) => (
  <Row key={index} data={item} index={index} />
))}
```
```vue
<!-- Vue -->
<Row
  v-for="item in items"
  :key="item.id"
  :data="item"
/>

<Row
  v-for="(item, index) in items"
  :key="index"
  :data="item"
  :index="index"
/>
```

`:key` is required on `v-for`. Use stable IDs, not array indices when possible.

---

## state_to_ref

```jsx
// React
const [count, setCount] = useState(0)
const [name, setName] = useState('')
const [items, setItems] = useState<string[]>([])
const [obj, setObj] = useState({ x: 0, y: 0 })

// Usage
setCount(count + 1)
setCount(prev => prev + 1)
setObj({ ...obj, x: 10 })
```
```vue
<script setup lang="ts">
import { ref, reactive } from 'vue'

// Primitives → ref
const count = ref(0)
const name = ref('')
const items = ref<string[]>([])

// Objects with many fields → reactive (avoids .value everywhere)
const obj = reactive({ x: 0, y: 0 })

// Usage — ref needs .value in script
count.value++
count.value = count.value + 1
obj.x = 10  // reactive: direct mutation, no spread needed
</script>
```

Use `ref` for primitives and simple values. Use `reactive` for objects when
you'd otherwise be spreading (`{ ...obj, key: val }`).

---

## effect_to_watch

```jsx
// React — run on mount
useEffect(() => {
  fetchData()
}, [])

// React — run when dep changes
useEffect(() => {
  document.title = title
}, [title])

// React — cleanup
useEffect(() => {
  const sub = subscribe(id)
  return () => sub.unsubscribe()
}, [id])
```
```vue
<script setup lang="ts">
import { watch, onMounted, onUnmounted } from 'vue'

// Run on mount
onMounted(() => {
  fetchData()
})

// Run when dep changes (lazy — doesn't run on mount)
watch(title, (newTitle) => {
  document.title = newTitle
})

// Run immediately (like useEffect with dep)
watch(title, (newTitle) => {
  document.title = newTitle
}, { immediate: true })

// Cleanup
let sub: Subscription | null = null
watch(id, (newId, _oldId, onCleanup) => {
  sub = subscribe(newId)
  onCleanup(() => sub?.unsubscribe())
}, { immediate: true })
</script>
```

---

## memo_to_computed

```jsx
// React
const total = useMemo(() => items.reduce((a, b) => a + b.price, 0), [items])
const filtered = useMemo(() => list.filter(x => x.active), [list, filter])
```
```vue
<script setup lang="ts">
import { computed } from 'vue'

const total = computed(() => items.value.reduce((a, b) => a + b.price, 0))
const filtered = computed(() => list.value.filter(x => x.active))
</script>
```

Vue's `computed` is automatically reactive to any refs/reactives read inside it.
No dependency array needed.

---

## callback_to_function

```jsx
// React — useCallback prevents recreation on every render
const handleClick = useCallback(() => {
  doThing(id)
}, [id])

const handleChange = useCallback((e) => {
  setValue(e.target.value)
}, [])
```
```vue
<script setup lang="ts">
// Vue — plain functions are fine; <script setup> runs once per instance
function handleClick() {
  doThing(id.value)
}

function handleChange(e: Event) {
  value.value = (e.target as HTMLInputElement).value
}
</script>
```

`useCallback` has no Vue equivalent because functions in `<script setup>` are
created once per component instance, not on every render.

---

## context_to_provide_inject

```jsx
// React — provider
const ThemeContext = createContext(defaultTheme)
function App() {
  return (
    <ThemeContext.Provider value={theme}>
      <Child />
    </ThemeContext.Provider>
  )
}

// React — consumer
function Child() {
  const theme = useContext(ThemeContext)
}
```
```vue
<!-- Vue — provider component -->
<script setup lang="ts">
import { provide } from 'vue'
import type { Theme } from './types'

const theme = ref<Theme>(defaultTheme)
provide('theme', theme)  // provide reactive ref so consumers see updates
</script>

<!-- Vue — consumer component -->
<script setup lang="ts">
import { inject } from 'vue'
import type { Theme } from './types'
import { Ref } from 'vue'

const theme = inject<Ref<Theme>>('theme')!
</script>
```

Use a Symbol key for type safety in larger projects:
```ts
// keys.ts
export const themeKey = Symbol() as InjectionKey<Ref<Theme>>
```

---

## ref_to_template_ref

```jsx
// React
const inputRef = useRef<HTMLInputElement>(null)
// in JSX:
<input ref={inputRef} />
// usage:
inputRef.current?.focus()
```
```vue
<template>
  <input ref="inputEl" />
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

const inputEl = ref<HTMLInputElement | null>(null)

onMounted(() => {
  inputEl.value?.focus()
})
</script>
```

The `ref` attribute in the template must match the variable name declared with `ref()`.

---

## forward_ref

```jsx
// React
const Input = forwardRef<HTMLInputElement, InputProps>((props, ref) => (
  <input ref={ref} {...props} />
))

// With useImperativeHandle
const Modal = forwardRef<ModalHandle, ModalProps>((props, ref) => {
  useImperativeHandle(ref, () => ({
    open: () => setVisible(true),
    close: () => setVisible(false),
  }))
  return <div>...</div>
})
```
```vue
<!-- Vue — expose public API with defineExpose -->
<script setup lang="ts">
import { ref } from 'vue'

const visible = ref(false)

defineExpose({
  open: () => { visible.value = true },
  close: () => { visible.value = false },
})
</script>
```

For forwarding the actual DOM element, parent uses `ref` on the component and
accesses `componentRef.value.$el` or use `defineExpose({ el: inputEl })`.

---

## component_props

```jsx
// React
interface SidebarProps {
  items: Item[]
  selected?: string
  onSelect?: (id: string) => void
}

function Sidebar({ items, selected = '', onSelect }: SidebarProps) {
```
```vue
<script setup lang="ts">
interface SidebarProps {
  items: Item[]
  selected?: string
  onSelect?: (id: string) => void
}

const props = withDefaults(defineProps<SidebarProps>(), {
  selected: '',
})
</script>
```

Callback props (`onSelect`) stay in the interface if the parent passes them as
props — but prefer emits for v0 (see `component_emits`).

---

## component_emits

```jsx
// React — callback props
interface ButtonProps {
  onClose: () => void
  onSelect: (id: string) => void
}

// Usage in parent:
<Button onClose={handleClose} onSelect={handleSelect} />
```
```vue
<!-- Vue — defineEmits -->
<script setup lang="ts">
const emit = defineEmits<{
  close: []
  select: [id: string]
}>()

// Call like:
emit('close')
emit('select', itemId)
</script>

<!-- Usage in parent: -->
<Button @close="handleClose" @select="handleSelect" />
```

Convention: React `onFoo` → Vue event name `foo`. The `on` prefix is dropped.

---

## fragments

```jsx
// React — fragments avoid extra DOM nodes
return (
  <>
    <Header />
    <Main />
    <Footer />
  </>
)
```
```vue
<!-- Vue 3 — multiple root elements are supported natively -->
<template>
  <Header />
  <Main />
  <Footer />
</template>
```

No wrapper needed. Vue 3 templates support multiple root elements.

---

## portals

```jsx
// React
import { createPortal } from 'react-dom'

function Modal({ children }) {
  return createPortal(
    <div className="modal">{children}</div>,
    document.body
  )
}
```
```vue
<!-- Vue — Teleport -->
<template>
  <Teleport to="body">
    <div class="modal">
      <slot />
    </div>
  </Teleport>
</template>
```

`Teleport` is built into Vue 3. Use `to="body"` for modals/tooltips/popovers.

---

## children_prop

```jsx
// React
function Card({ children }: { children: React.ReactNode }) {
  return <div className="card">{children}</div>
}

// Usage
<Card>
  <p>Content here</p>
</Card>
```
```vue
<!-- Vue — default slot -->
<template>
  <div class="card">
    <slot />
  </div>
</template>

<!-- Usage -->
<Card>
  <p>Content here</p>
</Card>
```

`{children}` always becomes `<slot />`. No prop needed in `defineProps`.

---

## named_children

```jsx
// React — render props / named children
function Layout({ header, sidebar, children }) {
  return (
    <div>
      <aside>{sidebar}</aside>
      <header>{header}</header>
      <main>{children}</main>
    </div>
  )
}

// Usage
<Layout
  header={<TopBar />}
  sidebar={<NavPanel />}
>
  <PageContent />
</Layout>
```
```vue
<!-- Vue — named slots -->
<template>
  <div>
    <aside><slot name="sidebar" /></aside>
    <header><slot name="header" /></header>
    <main><slot /></main>
  </div>
</template>

<!-- Usage -->
<Layout>
  <template #sidebar><NavPanel /></template>
  <template #header><TopBar /></template>
  <PageContent />
</Layout>
```

React render props (function-as-child) → Vue scoped slots:
```vue
<!-- Provider exposes data via scoped slot -->
<slot :item="currentItem" :index="currentIndex" />

<!-- Consumer -->
<Provider v-slot="{ item, index }">
  <Row :item="item" :index="index" />
</Provider>
```

---

## v_model

```jsx
// React — controlled input
const [value, setValue] = useState('')
<input value={value} onChange={(e) => setValue(e.target.value)} />
```
```vue
<!-- Vue — v-model shorthand -->
<script setup lang="ts">
const value = ref('')
</script>

<template>
  <input v-model="value" />
</template>
```

For custom components:
```vue
<!-- Parent -->
<MyInput v-model="name" />

<!-- MyInput.vue -->
<script setup lang="ts">
const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>
<template>
  <input :value="props.modelValue" @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)" />
</template>
```

---

## key_prop

```jsx
// React
<Component key={item.id} />
```
```vue
<!-- Vue — same syntax -->
<Component :key="item.id" />
```

Static string keys don't need `:`: `<div key="header">`. Dynamic values need `:key`.

---

## default_props

```jsx
// React
function Button({ variant = 'primary', size = 'md', disabled = false }) {
```
```vue
<script setup lang="ts">
interface ButtonProps {
  variant?: string
  size?: string
  disabled?: boolean
}

const props = withDefaults(defineProps<ButtonProps>(), {
  variant: 'primary',
  size: 'md',
  disabled: false,
})
</script>
```

---

## spread_props

```jsx
// React — spread all props to child
function Wrapper({ className, ...rest }) {
  return <button className={`wrapper ${className}`} {...rest} />
}
```
```vue
<!-- Vue — $attrs contains non-prop attributes -->
<script setup lang="ts">
// Disable automatic attribute inheritance so we can place it manually
defineOptions({ inheritAttrs: false })

interface WrapperProps {
  class?: string
}
const props = defineProps<WrapperProps>()
</script>

<template>
  <button :class="`wrapper ${props.class ?? ''}`" v-bind="$attrs" />
</template>
```

If `inheritAttrs: false` is not needed and all attrs should fall through to root,
just omit it — Vue's default behavior inherits all attrs on the root element.

---

## reducer_to_reactive

```jsx
// React
const [state, dispatch] = useReducer(reducer, initialState)
dispatch({ type: 'INCREMENT' })
dispatch({ type: 'SET_NAME', payload: name })
```
```vue
<script setup lang="ts">
import { reactive } from 'vue'

const state = reactive({ ...initialState })

function dispatch(action: { type: string; payload?: unknown }) {
  switch (action.type) {
    case 'INCREMENT': state.count++; break
    case 'SET_NAME': state.name = action.payload as string; break
  }
}
</script>
```

For complex state, prefer Pinia store (see `pinia_store`).

---

## pinia_store

```jsx
// React — Zustand / Context + useReducer global store equivalent
const useStore = create((set) => ({
  count: 0,
  increment: () => set(state => ({ count: state.count + 1 })),
}))
```
```ts
// Vue — Pinia store (stores/counter.ts)
import { defineStore } from 'pinia'

export const useCounterStore = defineStore('counter', () => {
  const count = ref(0)
  function increment() { count.value++ }
  return { count, increment }
})
```
```vue
<script setup lang="ts">
import { useCounterStore } from '@/stores/counter'
const counter = useCounterStore()
</script>

<template>
  <button @click="counter.increment">{{ counter.count }}</button>
</template>
```

---

## async_component

```jsx
// React — lazy loading
const Panel = React.lazy(() => import('./Panel'))
```
```vue
<script setup lang="ts">
import { defineAsyncComponent } from 'vue'

const Panel = defineAsyncComponent(() => import('./Panel.vue'))
</script>
```

---

## error_boundary

```jsx
// React — class-based error boundary
class ErrorBoundary extends React.Component { ... }
```
```vue
<!-- Vue — onErrorCaptured hook -->
<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'

const error = ref<Error | null>(null)
onErrorCaptured((err) => {
  error.value = err
  return false  // prevent propagation
})
</script>

<template>
  <slot v-if="!error" />
  <div v-else class="error">{{ error.message }}</div>
</template>
```

---

## import_substitutions

Direct package name substitutions when porting React packages to Vue equivalents:

| React package | Vue equivalent |
|---------------|----------------|
| `lucide-react` | `lucide-vue-next` |
| `framer-motion` | `@vueuse/motion` |
| `react-hook-form` | `vee-validate` or `@vueuse/core` |
| `react-router-dom` | `vue-router` |
| `react-query` | `@tanstack/vue-query` |
| `react-spring` | `@vueuse/motion` |
| `classnames` / `clsx` | `clsx` (same package, works in Vue) |
| `@radix-ui/*` | `radix-vue` |
| `shadcn/ui` | `shadcn-vue` |

```jsx
// React
import { ChevronRight, X, Check } from 'lucide-react'
import { motion } from 'framer-motion'
```
```vue
<script setup lang="ts">
import { ChevronRight, X, Check } from 'lucide-vue-next'
import { Motion } from '@vueuse/motion'
</script>
```

---

## claude_design_globals

Claude Design artifacts use global token objects (`wfColors`, `mfColors`, `wfFonts`)
defined inline in the source HTML. Phase A writes these to `src/design-tokens.ts`.

```jsx
// React source (inline in HTML)
const wfColors = { brand: '#2563eb', surface: '#f8fafc', ... }
const wfFonts = { sans: 'Inter, system-ui', mono: 'JetBrains Mono, monospace' }

// Usage in components
<div style={{ color: wfColors.brand }}>
<p style={{ fontFamily: wfFonts.sans }}>
```
```vue
<script setup lang="ts">
import { wfColors, wfFonts } from '@/design-tokens'
</script>

<template>
  <div :style="{ color: wfColors.brand }">
  <p :style="{ fontFamily: wfFonts.sans }">
</template>
```

`wfColors` / `mfColors` / `wfFonts` are plain objects (not reactive), so import
them directly — no `ref()` wrapper needed. They are read-only design constants.

---

## watchers_deep

```jsx
// React — deep object watching via JSON.stringify or custom equality
useEffect(() => {
  syncToBackend(formData)
}, [JSON.stringify(formData)])
```
```vue
<script setup lang="ts">
import { watch, reactive } from 'vue'

const formData = reactive({ name: '', email: '' })

// Watch deep — fires on any nested mutation
watch(formData, (newVal) => {
  syncToBackend(newVal)
}, { deep: true })
</script>
```

---

## template_refs_multiple

```jsx
// React — array of refs for list items
const itemRefs = useRef<(HTMLDivElement | null)[]>([])
items.forEach((_, i) => {
  itemRefs.current[i] = itemRefs.current[i] || null
})
```
```vue
<script setup lang="ts">
import { ref } from 'vue'

const itemRefs = ref<HTMLDivElement[]>([])
</script>

<template>
  <div
    v-for="(item, i) in items"
    :key="item.id"
    :ref="(el) => { if (el) itemRefs.value[i] = el as HTMLDivElement }"
  >
    {{ item.name }}
  </div>
</template>
```

---

## nextTick

```jsx
// React — wait for DOM update after state change
setState(newVal)
// Then read DOM — need flushSync or useLayoutEffect
import { flushSync } from 'react-dom'
flushSync(() => setState(newVal))
const height = divRef.current.offsetHeight
```
```vue
<script setup lang="ts">
import { nextTick } from 'vue'

async function updateAndRead() {
  count.value = newVal
  await nextTick()  // wait for DOM to update
  const height = divEl.value?.offsetHeight
}
</script>
```

---

## icon_objects

Icon objects (e.g. `MfIcons`, `WfIcons`) are exported from `@/design-tokens` as
`Record<string, string>` where each value is an SVG HTML string.
Use `v-html` to render them — do NOT use `<component :is>`.

```jsx
// React
import { MfIcons } from './tokens'
<div>{MfIcons.cursor}</div>
<div>{tool.icon}</div>
```
```vue
<!-- Vue -->
import { MfIcons } from '@/design-tokens'
<span v-html="MfIcons.cursor" />
<span v-html="tool.icon" />
```

If iterating over a tools array where `icon` is a string from an icon object:
```vue
<span v-if="tool.icon" v-html="tool.icon" />
```
