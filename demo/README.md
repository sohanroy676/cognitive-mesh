# Demo: two recorded chats, and how to run it live

| File | What it is |
|---|---|
| `discount-recording.json` | The models' replies for the request *"Customers should be able to apply a discount code to their cart."* on the small sample project `shop-api`. |
| `deepseek-recording.json` | The replies for *"Add DeepSeek as another model our multi-agent orchestration can use. The key will come from the environment, like the other providers."* on Cognitive Mesh itself. |
| `shop-api/` | The sample project (a cart and a pricing function, plus its check `check_shop.py`). |
| `make_shop_api.py` | Turns `shop-api/` into a git repository anywhere you choose (optionally with a GitHub `origin`). |
| `check_github.py` | Checks that everything a real push and pull request needs is in place. |
| `make_recording.py` | Turns what `serve --capture` saved into a new recording. |

## 1. Show the two chats on another computer (no keys, no model, no internet)

Needed there: `git` and [`uv`](https://docs.astral.sh/uv/). Install once **while online**, before the
presentation (about 2 GB of Python packages):

```bash
git clone <your repository URL> cognitive-mesh
cd cognitive-mesh
git checkout demo-Web-app
uv sync
```

Start the app in demo mode:

```bash
uv run cognitive-mesh serve --home demo-home \
  --replay demo/discount-recording.json --replay demo/deepseek-recording.json
```

Open the **chat** link it prints. After about a minute (the second chat reads the whole repository)
the sidebar lists both under **Previous chats**. Each is a real turn:

- the plan, with every agent's task, input, output and the model that wrote it;
- the files that were read, and the diff applied in a separate git worktree on its own branch;
- the contract check, the stored decisions and model calls (open **Trace**), and
- **Approve / Revise / Reject** buttons that work. Approve records your decision and the commit; it then
  offers the pull request (which needs the GitHub setup in section 3).

What is replayed, and what is not: the *text the models wrote* comes from the recording file; everything
else is the real application running on this computer. The header shows a small **demo mode** tag. Because
the replies are fixed, only the two recorded requests are accepted (pressing **Send** with the same text
runs the chat again from the start), and **Revise** cannot ask a model for a new draft.

Stop with Ctrl+C. Delete the `demo-home` folder to start from scratch (approvals do not survive a
restart; the chats are rebuilt each time).

## 2. Run it live

You need your own keys in a `.env` file in the repository root (git-ignored; never paste it anywhere):

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
COGMESH_ALLOW_REMOTE_CONTENT=true
COGMESH_ALLOW_TRAINING_USE=true
```

1. `uv run python demo/make_shop_api.py ../shop-api`
2. `uv run cognitive-mesh serve --home live-home --allow-unisolated-checks`
   (a project's check then runs on your computer, not isolated)
3. In the chat add `../shop-api` as a project, **Confirm dependencies and start**, choose **Make a small
   change** and type any request, for example the one in the recording.
4. Declare the project's check once (same `--home`), so a change is tested and a pull request is allowed:

   ```bash
   uv run cognitive-mesh workspace set-check --workspace live-home/workspace.sqlite3 --by presenter shop-api -- python check_shop.py
   ```

Models differ every time, so a live run will not match the recording. Free-tier keys have daily limits;
when one model is limited the next is tried.

## 3. Send an approved change to GitHub (pull request)

This sends the branch and its diff to GitHub, so it is a separate, deliberate step.

1. Create an **empty** GitHub repository, for example `shop-api-demo` (no README).
2. Create a **fine-grained personal access token** for that one repository with *Contents: Read and
   write* and *Pull requests: Read and write*. Put it in `.env` as `GITHUB_TOKEN=...`.
3. Create the sample with that remote and publish `main` yourself (a pull request needs a base branch):

   ```bash
   uv run python demo/make_shop_api.py ../shop-api --origin https://github.com/OWNER/shop-api-demo.git
   git -C ../shop-api push -u origin main
   ```

4. Check the setup. Without `--online` nothing leaves the computer; with it GitHub is asked whether
   the token can see and push to the repository:

   ```bash
   uv run python demo/check_github.py ../shop-api --online
   ```

5. Run a change (live, or the discount chat in demo mode after adding the project with the check
   declared and `--allow-unisolated-checks`). After **Approve**, **Push branch and open pull request**
   pushes the branch `cogmesh/change-...` and opens the pull request. Nothing is merged. The page
   says why if it is refused: no token, origin not on github.com, token cannot see the repository, or
   no check passed for the approved commit.

## 4. Make a new recording

```bash
uv run cognitive-mesh serve --home rec-home --capture captures        # live models, replies are saved
# ... send the request in the chat; the replies of each turn go to captures/turn-N.json
uv run python demo/make_recording.py captures/turn-N.json --title "..." --project shop-api \
  --sample shop-api --check python check_shop.py --out demo/new-recording.json
```

For a change to Cognitive Mesh itself use `--checkout` instead of `--sample`. A recording's diffs apply
to the files as they were when it was recorded, so record again if those files change.

## About the DeepSeek recording

The agents read the provider code, and the coder proposed a `deepseek` branch in
`providers/factory.py`. It is **not a complete change**: `COGMESH_PROVIDER` accepts only `groq`,
`ollama` or `catalog`, so that branch could never be reached. The simpler route is a catalog entry in
`docs/serve-catalog.toml` plus a key line in `.env.example`, which needs no code. Model output is
unverified, and no check ran on that project; an approval is your label, not a test.

## Not verified yet

Both recordings were replayed on this Windows computer only. A real push and pull request have not
been run (no repository or token yet), and DeepSeek itself has never been called.
