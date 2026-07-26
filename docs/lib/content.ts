import type { ComponentType } from "react";
import Agents from "@/content/agents.mdx";
import Application from "@/content/application.mdx";
import Architecture from "@/content/architecture.mdx";
import Authentication from "@/content/authentication.mdx";
import Cli from "@/content/cli.mdx";
import Client from "@/content/client.mdx";
import Comparisons from "@/content/comparisons.mdx";
import Concepts from "@/content/concepts.mdx";
import Configuration from "@/content/configuration.mdx";
import Contracts from "@/content/contracts.mdx";
import Database from "@/content/database.mdx";
import Deployment from "@/content/deployment.mdx";
import Errors from "@/content/errors.mdx";
import Execution from "@/content/execution.mdx";
import ExistingProject from "@/content/existing-project.mdx";
import GettingStarted from "@/content/getting-started.mdx";
import Idempotency from "@/content/idempotency.mdx";
import Overview from "@/content/index.mdx";
import Mcp from "@/content/mcp.mdx";
import Observability from "@/content/observability.mdx";
import OpenApi from "@/content/openapi.mdx";
import Pagination from "@/content/pagination.mdx";
import Preflight from "@/content/preflight.mdx";
import Production from "@/content/production.mdx";
import RateLimits from "@/content/rate-limits.mdx";
import Reference from "@/content/reference.mdx";
import Reliability from "@/content/reliability.mdx";
import Responses from "@/content/responses.mdx";
import Server from "@/content/server.mdx";
import Stability from "@/content/stability.mdx";
import Tasks from "@/content/tasks.mdx";
import Testing from "@/content/testing.mdx";
import Webhooks from "@/content/webhooks.mdx";
import type { DocsPath } from "@/lib/docs";

export const docsContent: Record<DocsPath, ComponentType> = {
  "/": Overview,
  "/getting-started": GettingStarted,
  "/existing-project": ExistingProject,
  "/concepts": Concepts,
  "/architecture": Architecture,
  "/comparisons": Comparisons,
  "/contracts": Contracts,
  "/application": Application,
  "/server": Server,
  "/responses": Responses,
  "/errors": Errors,
  "/client": Client,
  "/pagination": Pagination,
  "/authentication": Authentication,
  "/execution": Execution,
  "/tasks": Tasks,
  "/testing": Testing,
  "/production": Production,
  "/configuration": Configuration,
  "/database": Database,
  "/idempotency": Idempotency,
  "/rate-limits": RateLimits,
  "/webhooks": Webhooks,
  "/reliability": Reliability,
  "/observability": Observability,
  "/preflight": Preflight,
  "/deployment": Deployment,
  "/openapi": OpenApi,
  "/cli": Cli,
  "/agents": Agents,
  "/mcp": Mcp,
  "/reference": Reference,
  "/stability": Stability,
};
