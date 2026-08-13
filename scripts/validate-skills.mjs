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

function skillDirectories(root) {
  const directories = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;

    const directory = path.join(root, entry.name);
    if (fs.existsSync(path.join(directory, "SKILL.md"))) {
      directories.push(directory);
    }
    directories.push(...skillDirectories(directory));
  }
  return directories;
}

const skillDirs = skillDirectories(skillsDir);
let checked = 0;

for (const skillDir of skillDirs) {
  const skillName = path.basename(skillDir);
  const skillFile = path.join(skillDir, "SKILL.md");

  checked += 1;
  const skillVersion = readSkillVersion(skillFile);
  if (!skillVersion) {
    fail(`${skillName}: SKILL.md has no frontmatter version`);
    continue;
  }

  const evalFile = path.join(skillDir, "evals", "evals.json");
  if (!fs.existsSync(evalFile)) {
    console.log(`OK   ${skillName}@${skillVersion} (no evals.json)`);
    continue;
  }

  let evalData;
  try {
    evalData = JSON.parse(fs.readFileSync(evalFile, "utf8"));
  } catch (error) {
    fail(`${skillName}: invalid evals/evals.json (${error.message})`);
    continue;
  }

  if (evalData.skill_name !== skillName) {
    fail(`${skillName}: evals.json skill_name is '${evalData.skill_name ?? "missing"}'`);
    continue;
  }

  if (evalData.version !== skillVersion) {
    fail(`${skillName}: SKILL.md=${skillVersion}, evals.json=${evalData.version ?? "missing"}`);
    continue;
  }

  console.log(`OK   ${skillName}@${skillVersion}`);
}

if (checked === 0) {
  fail("no SKILL.md found under skills/");
}

if (!process.exitCode) {
  console.log(`Validated ${checked} skill(s).`);
}
