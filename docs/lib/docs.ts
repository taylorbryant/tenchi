export const siteName = "Tenchi";

export const siteDescription =
  "A contract-first, Python-native framework for typed JSON APIs that stay coherent as they grow.";

export const defaultSiteUrl = "https://tenchi.io";

export const docsSections = [
  {
    label: "Start",
    routes: [
      {
        path: "/",
        title: "Tenchi",
        navLabel: "Overview",
        description: siteDescription,
        priority: 1,
      },
      {
        path: "/getting-started",
        title: "Quickstart",
        description:
          "Create a Tenchi application, run its checks, make a contract change, and inspect the generated HTTP and OpenAPI surfaces.",
        priority: 0.95,
      },
      {
        path: "/existing-project",
        title: "Add to an existing project",
        navLabel: "Existing project",
        description:
          "Add Tenchi to an existing Python project and build the first complete contract, use case, ASGI application, test, and OpenAPI baseline.",
        priority: 0.94,
      },
      {
        path: "/concepts",
        title: "Mental model",
        description:
          "Learn the small Tenchi vocabulary: contracts, routes, use cases, ports, contexts, hooks, policies, and adapters.",
        priority: 0.94,
      },
      {
        path: "/architecture",
        title: "App architecture",
        description:
          "Understand where Tenchi applications place features, use cases, policies, ports, infrastructure, and server composition.",
        priority: 0.93,
      },
      {
        path: "/comparisons",
        title: "Comparisons",
        description:
          "Compare Tenchi with FastAPI, Starlette, Litestar, and Django Ninja, including when another framework is the better choice.",
        priority: 0.9,
      },
    ],
  },
  {
    label: "Core app model",
    routes: [
      {
        path: "/contracts",
        title: "Contracts",
        description:
          "Declare methods, paths, validated inputs, responses, headers, errors, media types, metadata, and runtime limits.",
        priority: 0.9,
      },
      {
        path: "/application",
        title: "Use cases and ports",
        description:
          "Keep application behavior in plain async functions and infrastructure behind app-owned typing.Protocol ports.",
        priority: 0.87,
      },
      {
        path: "/server",
        title: "Routes and server",
        description:
          "Bind contracts to use cases, compose route groups, and create an ASGI application with explicit context wiring.",
        priority: 0.88,
      },
      {
        path: "/responses",
        title: "Responses",
        description:
          "Model fixed and status-dependent successful responses, typed headers, media types, and Starlette passthrough responses.",
        priority: 0.83,
      },
      {
        path: "/errors",
        title: "Errors",
        description:
          "Declare stable application errors and understand Tenchi's honest server and client error semantics.",
        priority: 0.84,
      },
      {
        path: "/client",
        title: "Typed client",
        description:
          "Call Tenchi contracts through an async httpx client with validated inputs, bodies, headers, errors, and status-specific responses.",
        priority: 0.84,
      },
      {
        path: "/pagination",
        title: "Pagination",
        description:
          "Declare validated limit and offset queries and return typed page envelopes shared by the server, client, and OpenAPI.",
        priority: 0.8,
      },
    ],
  },
  {
    label: "Application design",
    routes: [
      {
        path: "/authentication",
        title: "Authentication and authorization",
        navLabel: "Authentication",
        description:
          "Authenticate at the HTTP boundary, authorize in use cases, and keep policies pure and reusable.",
        priority: 0.82,
      },
      {
        path: "/execution",
        title: "Workers and scripts",
        description:
          "Run use cases outside HTTP with the same request validation and context-scoping guarantees.",
        priority: 0.78,
      },
      {
        path: "/testing",
        title: "Testing",
        description:
          "Test use cases directly and exercise complete applications through lifespan-aware typed and raw in-process clients.",
        priority: 0.82,
      },
    ],
  },
  {
    label: "Operations",
    routes: [
      {
        path: "/openapi",
        title: "OpenAPI and compatibility",
        navLabel: "OpenAPI",
        description:
          "Generate OpenAPI 3.1, store canonical snapshots, and classify contract changes against a historical baseline.",
        priority: 0.86,
      },
      {
        path: "/cli",
        title: "CLI",
        description:
          "Create applications, preview generated slices, map application architecture, manage OpenAPI snapshots, and run versioned agent-readable checks.",
        priority: 0.85,
      },
      {
        path: "/agents",
        title: "Coding agents",
        navLabel: "Agents",
        description:
          "Use Tenchi's deterministic maps, mutation previews, structured diagnostics, and complete validation loop from a coding agent.",
        priority: 0.84,
      },
      {
        path: "/deployment",
        title: "Deployment",
        description:
          "Prepare a Tenchi ASGI application for production with lifecycle resources, middleware, health checks, and operational safeguards.",
        priority: 0.75,
      },
    ],
  },
  {
    label: "Reference",
    routes: [
      {
        path: "/reference",
        title: "Module reference",
        description:
          "Map Tenchi's public modules to the declarations and runtime helpers each one owns.",
        priority: 0.72,
      },
      {
        path: "/stability",
        title: "Stability and releases",
        description:
          "Understand Tenchi's pre-1.0 compatibility expectations, release process, and safe upgrade workflow.",
        priority: 0.7,
      },
    ],
  },
] as const;

export type DocsRoute = (typeof docsSections)[number]["routes"][number];
export type DocsPath = DocsRoute["path"];

export const docsRoutes: readonly DocsRoute[] = docsSections.reduce<
  DocsRoute[]
>((routes, section) => {
  routes.push(...section.routes);
  return routes;
}, []);

export function getDocsRoute(path: DocsPath) {
  return docsRoutes.find((route) => route.path === path);
}

export function getAdjacentDocsRoutes(path: DocsPath): {
  previous: DocsRoute | undefined;
  next: DocsRoute | undefined;
} {
  const index = docsRoutes.findIndex((route) => route.path === path);
  return {
    previous: index > 0 ? docsRoutes[index - 1] : undefined,
    next: index < docsRoutes.length - 1 ? docsRoutes[index + 1] : undefined,
  };
}

export function getSectionLabel(path: string): string | undefined {
  return docsSections.find((section) =>
    section.routes.some((route) => route.path === path),
  )?.label;
}
