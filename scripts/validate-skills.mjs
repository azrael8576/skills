#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const skillsDir = path.join(root, "skills");

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exitCode = 1;
}

function readSkillVersion(skillFile) {
  const text = fs.readFileSync(skillFile, "utf8");
  const frontmatter = text.match(/^---\s*\n([\s\S]*?)\n---/);
  if (!frontmatter) return null;
  const version = frontmatter[1].match(/^version:\s*([^\s#]+)\s*$/m);
  return version?.[1] ?? null;
}

const entries = fs.readdirSync(skillsDir, { withFileTypes: true });
let checked = 0;

for (const entry of entries) {
  if (!entry.isDirectory()) continue;

  const skillDir = path.join(skillsDir, entry.name);
  const skillFile = path.join(skillDir, "SKILL.md");
  if (!fs.existsSync(skillFile)) continue;

  checked += 1;
  const skillVersion = readSkillVersion(skillFile);
  if (!skillVersion) {
    fail(`${entry.name}: SKILL.md has no frontmatter version`);
    continue;
  }

  const evalFile = path.join(skillDir, "evals", "evals.json");
  if (!fs.existsSync(evalFile)) {
    console.log(`OK   ${entry.name}@${skillVersion} (no evals.json)`);
    continue;
  }

  let evalData;
  try {
    evalData = JSON.parse(fs.readFileSync(evalFile, "utf8"));
  } catch (error) {
    fail(`${entry.name}: invalid evals/evals.json (${error.message})`);
    continue;
  }

  if (evalData.skill_name !== entry.name) {
    fail(`${entry.name}: evals.json skill_name is '${evalData.skill_name ?? "missing"}'`);
  }

  if (evalData.version !== skillVersion) {
    fail(`${entry.name}: SKILL.md=${skillVersion}, evals.json=${evalData.version ?? "missing"}`);
    continue;
  }

  console.log(`OK   ${entry.name}@${skillVersion}`);
}

if (checked === 0) {
  fail("no skills/*/SKILL.md found");
}

if (!process.exitCode) {
  console.log(`Validated ${checked} skill(s).`);
}
