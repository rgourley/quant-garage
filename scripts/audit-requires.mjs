#!/usr/bin/env node
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { parse } from "yaml";

const SKILLS_DIR = new URL("../skills/", import.meta.url).pathname;

const VALID_PRODUCTS = new Set([
  "stocks",
  "options",
  "crypto",
  "forex",
  "indices",
  "futures",
  "benzinga_news",
  "benzinga_earnings",
  "benzinga_analyst_ratings",
  "benzinga_sentiment",
  "any",
]);
const VALID_TIERS = new Set([
  "basic",
  "starter",
  "developer",
  "advanced",
  "business",
  "addon",
]);
const VALID_INTERFACES = new Set(["rest", "flat-files", "websocket"]);
const VALID_KINDS = new Set(["foundation", "skill"]);
const VALID_OUTPUT_MODES = new Set([
  "note",
  "stream",
  "table",
  "exception-report",
  "list",
  "dataset",
  "hybrid",
]);

async function main() {
  const entries = await readdir(SKILLS_DIR, { withFileTypes: true });
  const skillDirs = entries.filter((e) => e.isDirectory()).map((e) => e.name);

  const issues = [];
  const knownNames = new Set();
  const declaredFallbacks = [];

  for (const dir of skillDirs) {
    const yamlPath = join(SKILLS_DIR, dir, "requires.yml");
    const skillPath = join(SKILLS_DIR, dir, "SKILL.md");

    let raw;
    try {
      raw = await readFile(yamlPath, "utf8");
    } catch {
      issues.push({ skill: dir, problem: "missing requires.yml" });
      continue;
    }

    try {
      await readFile(skillPath, "utf8");
    } catch {
      issues.push({ skill: dir, problem: "missing SKILL.md" });
    }

    let doc;
    try {
      doc = parse(raw);
    } catch (err) {
      issues.push({ skill: dir, problem: `invalid YAML: ${err.message}` });
      continue;
    }

    if (!doc?.name) {
      issues.push({ skill: dir, problem: "name missing" });
      continue;
    }
    if (doc.name !== dir) {
      issues.push({
        skill: dir,
        problem: `name "${doc.name}" doesn't match directory`,
      });
    }
    knownNames.add(doc.name);

    if (!doc.kind || !VALID_KINDS.has(doc.kind)) {
      issues.push({
        skill: dir,
        problem: `kind must be one of: ${[...VALID_KINDS].join(", ")}`,
      });
    }

    if (!doc.interface || !VALID_INTERFACES.has(doc.interface)) {
      issues.push({
        skill: dir,
        problem: `interface must be one of: ${[...VALID_INTERFACES].join(", ")}`,
      });
    }

    if (doc.kind === "skill") {
      if (!doc.output_mode || !VALID_OUTPUT_MODES.has(doc.output_mode)) {
        issues.push({
          skill: dir,
          problem: `output_mode must be one of: ${[...VALID_OUTPUT_MODES].join(", ")}`,
        });
      }

      const schemaPath = join(SKILLS_DIR, dir, "output-schema.json");
      try {
        const schemaRaw = await readFile(schemaPath, "utf8");
        JSON.parse(schemaRaw);
      } catch (err) {
        if (err.code === "ENOENT") {
          issues.push({
            skill: dir,
            problem:
              "missing output-schema.json (required for user-facing skills)",
          });
        } else {
          issues.push({
            skill: dir,
            problem: `output-schema.json is invalid JSON: ${err.message}`,
          });
        }
      }

      const renderingPath = join(SKILLS_DIR, dir, "references", "rendering.md");
      try {
        await readFile(renderingPath, "utf8");
      } catch {
        issues.push({
          skill: dir,
          problem:
            "missing references/rendering.md (required for user-facing skills)",
        });
      }
    }

    if (!Array.isArray(doc.requires) || doc.requires.length === 0) {
      issues.push({ skill: dir, problem: "requires must be a non-empty list" });
    } else {
      for (const req of doc.requires) {
        if (!VALID_PRODUCTS.has(req.product)) {
          issues.push({
            skill: dir,
            problem: `invalid product: ${req.product}`,
          });
        }
        if (!VALID_TIERS.has(req.tier)) {
          issues.push({ skill: dir, problem: `invalid tier: ${req.tier}` });
        }
      }
    }

    if (Array.isArray(doc.fallbacks)) {
      for (const fallback of doc.fallbacks) {
        declaredFallbacks.push({ skill: dir, fallback });
      }
    }
  }

  const warnings = [];
  for (const { skill, fallback } of declaredFallbacks) {
    if (!knownNames.has(fallback)) {
      warnings.push({
        skill,
        problem: `fallback "${fallback}" doesn't match any known skill (planned but not yet written?)`,
      });
    }
  }

  for (const warning of warnings) {
    console.warn(`WARN: ${warning.skill}: ${warning.problem}`);
  }

  if (issues.length === 0) {
    console.log(`OK: ${skillDirs.length} skills, no issues found.`);
    return;
  }

  console.error(`\nFAIL: ${issues.length} issues found:\n`);
  for (const issue of issues) {
    console.error(`  ${issue.skill}: ${issue.problem}`);
  }
  process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
