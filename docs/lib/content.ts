import type { ComponentType } from "react";
import Agents from "@/content/agents.mdx";
import Application from "@/content/application.mdx";
import Architecture from "@/content/architecture.mdx";
import Authentication from "@/content/authentication.mdx";
import Cli from "@/content/cli.mdx";
import Client from "@/content/client.mdx";
import Comparisons from "@/content/comparisons.mdx";
import Concepts from "@/content/concepts.mdx";
import Contracts from "@/content/contracts.mdx";
import Deployment from "@/content/deployment.mdx";
import Errors from "@/content/errors.mdx";
import Execution from "@/content/execution.mdx";
import GettingStarted from "@/content/getting-started.mdx";
import Overview from "@/content/index.mdx";
import OpenApi from "@/content/openapi.mdx";
import Pagination from "@/content/pagination.mdx";
import Reference from "@/content/reference.mdx";
import Responses from "@/content/responses.mdx";
import Server from "@/content/server.mdx";
import Stability from "@/content/stability.mdx";
import Testing from "@/content/testing.mdx";
import type { DocsPath } from "@/lib/docs";

export const docsContent: Record<DocsPath, ComponentType> = {
  "/": Overview,
  "/getting-started": GettingStarted,
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
  "/testing": Testing,
  "/openapi": OpenApi,
  "/cli": Cli,
  "/agents": Agents,
  "/deployment": Deployment,
  "/reference": Reference,
  "/stability": Stability,
};
