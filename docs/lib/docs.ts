export const siteName = "Tenchi";

export const siteDescription =
  "Build production Python backends that humans and coding agents can understand, change, and verify.";

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
        title: "Build and verify your first app",
        navLabel: "First application",
        description:
          "Create a complete Tenchi application, call its API, change a contract, and verify the result against an immutable baseline.",
        priority: 0.96,
      },
      {
        path: "/build-a-feature",
        title: "Build a feature end to end",
        navLabel: "Build a feature",
        description:
          "Carry one persisted operation through its contract, use case, port, adapters, route, tests, and verification receipt.",
        priority: 0.95,
      },
      {
        path: "/existing-project",
        title: "Add Tenchi to an existing project",
        navLabel: "Existing project",
        description:
          "Add Tenchi without the application generator and build the first complete contract, use case, ASGI application, test, and OpenAPI baseline.",
        priority: 0.94,
      },
      {
        path: "/concepts",
        title: "How Tenchi works",
        navLabel: "Mental model",
        description:
          "Understand the small application model shared by HTTP, workers, tools, tests, and coding-agent workflows.",
        priority: 0.94,
      },
      {
        path: "/comparisons",
        title: "Choose the right framework",
        navLabel: "Comparisons",
        description:
          "Compare Tenchi with FastAPI, Starlette, Litestar, and Django Ninja, including when another framework is the better choice.",
        priority: 0.9,
      },
    ],
  },
  {
    label: "Build",
    routes: [
      {
        path: "/architecture",
        title: "Structure your application",
        navLabel: "App architecture",
        description:
          "Place features, use cases, policies, ports, infrastructure, and server composition where their dependencies remain explicit.",
        priority: 0.93,
      },
      {
        path: "/contracts",
        title: "Declare an HTTP contract",
        navLabel: "Contracts",
        description:
          "Declare methods, paths, validated inputs, responses, headers, errors, media types, examples, and runtime limits.",
        priority: 0.9,
      },
      {
        path: "/application",
        title: "Write use cases and ports",
        navLabel: "Use cases and ports",
        description:
          "Keep behavior in plain async functions and infrastructure behind application-owned typing.Protocol ports.",
        priority: 0.89,
      },
      {
        path: "/server",
        title: "Bind routes and compose the server",
        navLabel: "Routes and server",
        description:
          "Bind contracts to use cases and create an ASGI application with explicit context, lifecycle, hook, and adapter wiring.",
        priority: 0.88,
      },
      {
        path: "/responses",
        title: "Model successful responses",
        navLabel: "Responses",
        description:
          "Model fixed and status-dependent success bodies, typed headers, media types, and controlled Starlette passthrough responses.",
        priority: 0.84,
      },
      {
        path: "/errors",
        title: "Expose honest application errors",
        navLabel: "Errors",
        description:
          "Declare stable application errors and keep server, OpenAPI, and typed-client error behavior aligned.",
        priority: 0.85,
      },
      {
        path: "/authentication",
        title: "Authenticate and authorize requests",
        navLabel: "Authentication",
        description:
          "Authenticate at the HTTP boundary, authorize in use cases, and keep policies pure and reusable from every entrypoint.",
        priority: 0.85,
      },
      {
        path: "/client",
        title: "Call contracts with the typed client",
        navLabel: "Typed client",
        description:
          "Call Tenchi contracts through async httpx with validated inputs, responses, errors, retries, and payload-safe outcomes.",
        priority: 0.84,
      },
      {
        path: "/pagination",
        title: "Paginate collection endpoints",
        navLabel: "Pagination",
        description:
          "Share validated limit and offset queries and typed page envelopes across the server, client, and OpenAPI.",
        priority: 0.8,
      },
      {
        path: "/testing",
        title: "Test at the right boundary",
        navLabel: "Testing",
        description:
          "Test use cases directly and exercise complete applications through lifespan-aware typed and raw in-process clients.",
        priority: 0.84,
      },
    ],
  },
  {
    label: "Ship",
    routes: [
      {
        path: "/production",
        title: "Prepare the application for production",
        navLabel: "Production handbook",
        description:
          "Turn Tenchi's application model into concrete decisions for configuration, transactions, retries, workers, telemetry, and deployment.",
        priority: 0.9,
      },
      {
        path: "/configuration",
        title: "Load configuration and secrets",
        navLabel: "Configuration",
        description:
          "Validate process configuration at startup, keep secrets out of application behavior, and wire settings at the composition root.",
        priority: 0.82,
      },
      {
        path: "/database",
        title: "Own database transactions",
        navLabel: "Databases",
        description:
          "Own pools with lifespan, create one unit of work per request, run migrations safely, and prevent lost updates.",
        priority: 0.84,
      },
      {
        path: "/idempotency",
        title: "Make operations retry-safe",
        navLabel: "Idempotency",
        description:
          "Reserve scoped keys, fingerprint validated input, and replay typed results without coupling application behavior to storage.",
        priority: 0.85,
      },
      {
        path: "/rate-limits",
        title: "Limit application operations",
        navLabel: "Rate limiting",
        description:
          "Apply authenticated fixed-window quotas with atomic shared storage and deterministic memory-backed tests.",
        priority: 0.83,
      },
      {
        path: "/webhooks",
        title: "Receive signed webhooks",
        navLabel: "Signed webhooks",
        description:
          "Verify exact request bytes before parsing, attach service identity, and make provider redeliveries idempotent.",
        priority: 0.83,
      },
      {
        path: "/reliability",
        title: "Design for retries and partial failure",
        navLabel: "Retries and workers",
        description:
          "Make commands safe to retry, commit deferred work through a transactional outbox, and classify worker failures.",
        priority: 0.84,
      },
      {
        path: "/execution",
        title: "Run use cases outside HTTP",
        navLabel: "Workers and scripts",
        description:
          "Run application behavior from workers and scripts with the same request validation and context-scoping guarantees.",
        priority: 0.81,
      },
      {
        path: "/jobs",
        title: "Dispatch validated background jobs",
        navLabel: "Background jobs",
        description:
          "Declare durable job messages, validate producers and consumers, and dispatch through queue-neutral handlers.",
        priority: 0.83,
      },
      {
        path: "/tasks",
        title: "Run validated operational tasks",
        navLabel: "Operational tasks",
        description:
          "Declare, discover, and safely run backfills, repairs, replays, and maintenance commands.",
        priority: 0.82,
      },
      {
        path: "/observability",
        title: "Observe behavior without exposing payloads",
        navLabel: "Observability",
        description:
          "Export stable outcomes through OpenTelemetry, instrument non-HTTP work, and keep durable audit records separate.",
        priority: 0.82,
      },
      {
        path: "/preflight",
        title: "Verify a deployment environment",
        navLabel: "Preflight",
        description:
          "Run read-only, timeout-bounded checks against the environment a release is about to use.",
        priority: 0.84,
      },
      {
        path: "/deployment",
        title: "Deploy the ASGI application",
        navLabel: "Deployment",
        description:
          "Configure the production process, lifecycle resources, middleware, health checks, and release gates.",
        priority: 0.8,
      },
      {
        path: "/openapi",
        title: "Review API compatibility",
        navLabel: "OpenAPI and compatibility",
        description:
          "Generate OpenAPI 3.1, store canonical snapshots, and classify contract changes against a historical baseline.",
        priority: 0.86,
      },
    ],
  },
  {
    label: "AI and agents",
    routes: [
      {
        path: "/ai",
        title: "Build with AI",
        description:
          "Use coding agents to change a Tenchi backend and expose application behavior safely to AI callers through the same architecture.",
        priority: 0.92,
      },
      {
        path: "/agents",
        title: "Let a coding agent change your backend",
        navLabel: "Coding agents",
        description:
          "Give coding agents deterministic maps, mutation previews, structured diagnostics, and a complete validation loop.",
        priority: 0.88,
      },
      {
        path: "/mcp",
        title: "Connect a coding agent over MCP",
        navLabel: "Coding-agent MCP",
        description:
          "Connect an MCP-aware coding agent to Tenchi's application map, generation previews, compatibility reports, and checks.",
        priority: 0.86,
      },
      {
        path: "/change-plans",
        title: "Verify a generated change",
        navLabel: "Change plans",
        description:
          "Tie contract-driven generation to a content-addressed plan and verify its structural postconditions against one Git baseline.",
        priority: 0.87,
      },
      {
        path: "/tools",
        title: "Expose use cases as AI tools",
        navLabel: "Application tools",
        description:
          "Give existing use cases stable names, typed inputs and outputs, declared errors, and safety metadata for machine callers.",
        priority: 0.88,
      },
      {
        path: "/tool-mcp",
        title: "Serve application tools over MCP",
        navLabel: "Application MCP",
        description:
          "Publish authenticated tools with caller-specific discovery, explicit destructive-call approval, and structured results.",
        priority: 0.87,
      },
      {
        path: "/evaluations",
        title: "Gate AI behavior with evaluations",
        navLabel: "AI evaluations",
        description:
          "Set typed cases, metric thresholds, execution limits, and token or cost budgets for application-owned AI behavior.",
        priority: 0.87,
      },
      {
        path: "/fieldnotes",
        title: "Study the cited AI reference backend",
        navLabel: "Fieldnotes example",
        description:
          "Run and adapt a cited research backend with background indexing, authenticated application tools, evidence, and evaluation gates.",
        priority: 0.84,
      },
    ],
  },
  {
    label: "Reference",
    routes: [
      {
        path: "/cli",
        title: "CLI reference",
        navLabel: "CLI",
        description:
          "Look up commands for scaffolding, generation, inspection, compatibility, operational entrypoints, and verification.",
        priority: 0.8,
      },
      {
        path: "/reference",
        title: "Python module reference",
        navLabel: "Python modules",
        description:
          "Map Tenchi's supported modules to the declarations, protocols, exceptions, and runtime helpers each one owns.",
        priority: 0.76,
      },
      {
        path: "/stability",
        title: "Upgrade Tenchi safely",
        navLabel: "Stability and releases",
        description:
          "Understand Tenchi's pre-1.0 compatibility expectations, versioned surfaces, and upgrade workflow.",
        priority: 0.74,
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
    section.routes.some((route) =>
      route.path === "/"
        ? path === "/"
        : path === route.path || path.startsWith(`${route.path}/`),
    ),
  )?.label;
}
