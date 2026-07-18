export const tenchiDarkTheme = {
  name: "tenchi-dark",
  displayName: "Tenchi Dark",
  type: "dark" as const,
  fg: "#dce7e1",
  bg: "#16271f",
  colors: {
    "editor.background": "#16271f",
    "editor.foreground": "#dce7e1",
    "editor.selectionBackground": "#24543d",
    "editor.lineHighlightBackground": "#1d3027",
  },
  tokenColors: [
    {
      scope: ["comment", "punctuation.definition.comment", "string.comment"],
      settings: { foreground: "#8a9a92", fontStyle: "italic" },
    },
    {
      scope: ["punctuation", "meta.brace", "delimiter", "delimiter.bracket"],
      settings: { foreground: "#9fb0a7" },
    },
    {
      scope: [
        "keyword",
        "storage.type",
        "storage.modifier",
        "punctuation.definition.template-expression",
      ],
      settings: { foreground: "#34d399" },
    },
    {
      scope: [
        "storage.modifier.package",
        "storage.modifier.import",
        "storage.type.java",
      ],
      settings: { foreground: "#c4d0ca" },
    },
    {
      scope: ["entity.name.function", "support.function"],
      settings: { foreground: "#a7f3d0" },
    },
    {
      scope: [
        "entity.name.type",
        "entity.name.class",
        "support.type",
        "support.class",
        "storage.type.class",
      ],
      settings: { foreground: "#5eead4" },
    },
    {
      scope: ["string", "attribute.value"],
      settings: { foreground: "#9bddaa" },
    },
    {
      scope: ["punctuation.definition.string"],
      settings: { foreground: "#75ad82" },
    },
    {
      scope: [
        "constant.numeric",
        "constant.language",
        "constant.character",
        "variable.language",
      ],
      settings: { foreground: "#fbbf24" },
    },
    {
      scope: [
        "meta.property-name",
        "meta.object-literal.key",
        "support.type.property-name.json",
        "entity.name.tag.yaml",
        "attribute.name",
      ],
      settings: { foreground: "#6ee7b7" },
    },
    {
      scope: [
        "variable",
        "identifier",
        "variable.parameter.function",
        "variable.other",
      ],
      settings: { foreground: "#dce7e1" },
    },
    {
      scope: ["keyword.operator", "keyword.operator.assignment"],
      settings: { foreground: "#86c9a8" },
    },
    {
      scope: ["source.regexp", "string.regexp"],
      settings: { foreground: "#fcd34d" },
    },
    {
      scope: ["invalid", "message.error"],
      settings: { foreground: "#fb7185", fontStyle: "italic" },
    },
    {
      scope: [
        "markup.inserted",
        "meta.diff.header.to-file",
        "punctuation.definition.inserted",
      ],
      settings: { foreground: "#86efac", background: "#123524" },
    },
    {
      scope: [
        "markup.deleted",
        "meta.diff.header.from-file",
        "punctuation.definition.deleted",
      ],
      settings: { foreground: "#fda4af", background: "#3f1d25" },
    },
    {
      scope: ["markup.heading", "meta.diff.range"],
      settings: { foreground: "#6ee7b7", fontStyle: "bold" },
    },
  ],
};
