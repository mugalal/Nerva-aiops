# GitHub workflow (beginner-proof)

No WhatsApp/USB file passing. One shared repo, branches + pull requests.

## First push (team lead, once)
```bash
git add -A
git commit -m "Day 1: contracts frozen, 10 mocks, demo scaffold, tooling"
gh repo create nexus-aiops --public --source=. --remote=origin --push
# without gh CLI: create empty repo on github.com, then:
# git remote add origin https://github.com/<org>/nexus-aiops.git
# git push -u origin main
```

## Branch protection (on GitHub: Settings → Branches)
- Protect `main`: require pull request before merging, require 1 review.

## Daily flow (every member)
```bash
git checkout main; git pull
git checkout -b m<N>/short-task        # e.g. m2/pydantic-contracts
# ... work, keep it small ...
git add -A; git commit -m "M2: add Pydantic contract models"
git push -u origin m<N>/short-task
# open PR to main on GitHub, ask 1 teammate to review, merge, delete branch
```

Rules: never commit to `main` directly. Never commit secrets/`.env`. Small PRs daily beat one giant PR on Day 5.
