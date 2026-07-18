import { describe, expect, test } from "bun:test";
import { tenchiDarkTheme } from "./syntax-theme";

describe("Tenchi dark syntax theme", () => {
  test("uses the documentation palette", () => {
    expect(tenchiDarkTheme.type).toBe("dark");
    expect(tenchiDarkTheme.bg).toBe("#16271f");
    expect(tenchiDarkTheme.fg).toBe("#dce7e1");
    expect(tenchiDarkTheme.colors["editor.background"]).toBe(
      tenchiDarkTheme.bg,
    );
  });

  test("styles the syntax roles used throughout the documentation", () => {
    const scopes = tenchiDarkTheme.tokenColors.flatMap((rule) => rule.scope);
    expect(scopes).toContain("comment");
    expect(scopes).toContain("keyword");
    expect(scopes).toContain("entity.name.function");
    expect(scopes).toContain("constant.numeric");
    expect(scopes).toContain("support.type.property-name.json");
    expect(scopes).toContain("markup.inserted");
    expect(scopes).toContain("markup.deleted");
  });
});
