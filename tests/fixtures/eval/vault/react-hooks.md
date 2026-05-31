# React Hooks: useEffect

useEffect runs side effects after a component renders. The dependency array controls
when it re-runs: an empty array runs the effect once on mount, while listed values
re-run it whenever any of them change. Omitting the array runs the effect after every
render. Returning a function from the effect registers cleanup that runs before the
next effect and on unmount. Missing dependencies cause stale closures that read old
state, a common source of bugs.
