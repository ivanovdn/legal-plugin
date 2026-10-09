// Compile-time constant from vite.config.ts `define`: the commit this bundle was
// built from. Undefined at runtime under tsx (unit tests, the eval runner), so
// read it only through paneBuild() in requestHeaders.ts.
declare const __PANE_BUILD__: string;
