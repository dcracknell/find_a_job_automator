const FIELD_MAP = [
  ["Domain", "domain"],
  ["CV text", "cv-text"],
  ["CV PDF attachment", null],
  ["Search city", "search-city"],
  ["Search radius miles", "search-radius"],
  ["Remote jobs", "remote-jobs"],
  ["Minimum salary GBP", "minimum-salary"],
  ["Core target roles", "core-roles"],
  ["Adjacent target roles", "adjacent-roles"],
  ["Stretch target roles", "stretch-roles"],
  ["Core skills", "core-skills"],
  ["Adjacent skills", "adjacent-skills"],
  ["Title words to exclude", "title-excludes"],
  ["Description terms to exclude", "description-excludes"],
  ["Companies to exclude", "company-excludes"],
];

const JUNIOR_MODIFIERS = ["junior", "graduate", "entry level", "junior/graduate"];
const MAX_PREFILL_LENGTH = 6200;

const repoInput = document.querySelector("#repo");
const issueBody = document.querySelector("#issue-body");
const queryList = document.querySelector("#query-list");
const queryCount = document.querySelector("#query-count");
const statusEl = document.querySelector("#status");
const form = document.querySelector("#profile-form");

function byId(id) {
  return document.getElementById(id);
}

function valueFor(id) {
  if (!id) {
    return "";
  }
  return byId(id).value.trim();
}

function inferRepo() {
  const saved = localStorage.getItem("jobSearchRepo");
  if (saved) {
    return saved;
  }

  const host = window.location.hostname;
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  if (host.endsWith(".github.io") && pathParts.length) {
    const owner = host.replace(".github.io", "");
    return `${owner}/${pathParts[0]}`;
  }
  return "";
}

function splitList(raw) {
  return raw
    .split(/[\n,;]+/)
    .map((item) => item.trim().replace(/^[-*]\s*/, ""))
    .filter(Boolean);
}

function addQuery(queries, seen, titleExcludes, query) {
  const cleaned = query.trim();
  if (!cleaned || seen.has(cleaned)) {
    return;
  }
  const lowered = cleaned.toLowerCase();
  if (titleExcludes.some((term) => term && lowered.includes(term.toLowerCase()))) {
    return;
  }
  seen.add(cleaned);
  queries.push(cleaned);
}

function generateQueries() {
  const city = valueFor("search-city");
  const remoteOk = valueFor("remote-jobs") !== "No";
  const coreRoles = splitList(valueFor("core-roles"));
  const adjacentRoles = splitList(valueFor("adjacent-roles"));
  const stretchRoles = splitList(valueFor("stretch-roles"));
  const coreSkills = splitList(valueFor("core-skills"));
  const titleExcludes = splitList(valueFor("title-excludes"));
  const queries = [];
  const seen = new Set();

  for (const role of coreRoles) {
    addQuery(queries, seen, titleExcludes, role);
    if (city) {
      addQuery(queries, seen, titleExcludes, `${role} ${city}`);
    }
    for (const mod of JUNIOR_MODIFIERS) {
      addQuery(queries, seen, titleExcludes, `${mod} ${role}`);
    }
    if (remoteOk) {
      addQuery(queries, seen, titleExcludes, `${role} remote`);
    }
  }

  for (const role of adjacentRoles) {
    addQuery(queries, seen, titleExcludes, role);
    if (city) {
      addQuery(queries, seen, titleExcludes, `${role} ${city}`);
    }
    if (remoteOk) {
      addQuery(queries, seen, titleExcludes, `${role} remote`);
    }
  }

  for (const role of stretchRoles) {
    addQuery(queries, seen, titleExcludes, role);
    if (city) {
      addQuery(queries, seen, titleExcludes, `${role} ${city}`);
    }
  }

  for (const skill of coreSkills.slice(0, 3)) {
    addQuery(queries, seen, titleExcludes, `${skill} engineer`);
    if (city) {
      addQuery(queries, seen, titleExcludes, `${skill} ${city}`);
    }
  }

  return queries.slice(0, 30);
}

function buildIssueBody() {
  const lines = [];
  for (const [heading, id] of FIELD_MAP) {
    let value = valueFor(id);
    if (heading === "CV PDF attachment" && !value) {
      value = "If you did not paste CV text, drag your PDF into this section before submitting.";
    }
    lines.push(`### ${heading}`);
    lines.push(value || "_No response_");
    lines.push("");
  }
  return lines.join("\n").trimEnd() + "\n";
}

function renderQueries() {
  const queries = generateQueries();
  queryList.replaceChildren();
  queryCount.textContent = String(queries.length);

  if (!queries.length) {
    const item = document.createElement("li");
    item.textContent = "Add target roles or core skills to preview generated searches.";
    queryList.append(item);
    return;
  }

  for (const query of queries) {
    const item = document.createElement("li");
    item.textContent = query;
    queryList.append(item);
  }
}

function setStatus(message, warn = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("warn", warn);
}

function update() {
  const body = buildIssueBody();
  issueBody.value = body;
  renderQueries();
  localStorage.setItem("jobSearchSetupDraft", JSON.stringify(readDraft()));
}

function readDraft() {
  const draft = { repo: repoInput.value.trim() };
  for (const [, id] of FIELD_MAP) {
    if (id) {
      draft[id] = valueFor(id);
    }
  }
  return draft;
}

function restoreDraft() {
  repoInput.value = inferRepo();

  try {
    const draft = JSON.parse(localStorage.getItem("jobSearchSetupDraft") || "{}");
    if (draft.repo) {
      repoInput.value = draft.repo;
    }
    for (const [, id] of FIELD_MAP) {
      if (id && Object.prototype.hasOwnProperty.call(draft, id)) {
        byId(id).value = draft[id];
      }
    }
  } catch {
    return;
  }
}

async function copyBody() {
  const body = buildIssueBody();
  await navigator.clipboard.writeText(body);
  setStatus("Copied. Paste it into a GitHub issue if the prefill is too large.");
}

async function openIssue() {
  const repo = repoInput.value.trim();
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) {
    setStatus("Enter the repo as owner/repo before opening GitHub.", true);
    repoInput.focus();
    return;
  }

  localStorage.setItem("jobSearchRepo", repo);
  const body = buildIssueBody();
  const base = `https://github.com/${repo}/issues/new`;
  const params = new URLSearchParams({
    title: "Job search profile setup",
    labels: "profile-setup",
  });

  if (body.length <= MAX_PREFILL_LENGTH) {
    params.set("body", body);
    window.open(`${base}?${params.toString()}`, "_blank", "noopener,noreferrer");
    setStatus("Opened GitHub with the issue prefilled. Submit it to start setup.");
    return;
  }

  await navigator.clipboard.writeText(body);
  window.open(`${base}?${params.toString()}`, "_blank", "noopener,noreferrer");
  setStatus("CV text is long, so the issue body was copied. Paste it into GitHub.", true);
}

restoreDraft();
update();

form.addEventListener("input", update);
repoInput.addEventListener("input", update);
byId("copy-body").addEventListener("click", copyBody);
byId("open-issue").addEventListener("click", openIssue);
