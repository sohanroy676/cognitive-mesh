# Demo: the Discount example

This folder lets you show Cognitive Mesh from another computer. There are two ways, and the first
needs no model, key or internet connection once the project is installed.

| File | What it is |
|---|---|
| `discount-example.json` | A recorded run: "add a `Discount` class to the shop cart". It holds the plan, what each agent did, the coder's drafts, the diff and the contract check. |
| `shop-api/` | The small sample project the run was made on (a cart and a pricing function, plus its check `check_shop.py`). |
| `make_shop_api.py` | Turns `shop-api/` into a git repository anywhere you choose (optionally with a GitHub `origin`). |
| `check_github.py` | Checks that everything a real push and pull request needs is in place. |
| `export_turn.py` | Saves another turn of your own home as a new recording. |

## 1. Show the recorded example (no keys, no model)

On the other computer you need `git` and [`uv`](https://docs.astral.sh/uv/). Do the install once
**while online**, before the presentation (it downloads about 2 GB of Python packages):

```bash
git clone <your repository URL> cognitive-mesh
cd cognitive-mesh
git checkout demo-Web-app
uv sync
```

Start the app with the recording:

```bash
uv run cognitive-mesh serve --home demo-home --replay demo/discount-example.json
```

Open the **chat** link it prints (keep the terminal running). The page shows the recorded turn,
labelled "Recorded example":

- the plan, with a plain-words task, input, output and model for every agent,
- the coder's drafts (two were refused, one applied), and
- the diff that adds `Discount` to `shop/cart.py` and lets `Cart` use it.

The approval buttons are off in a recording; nothing calls a model. The **Dependency graph** and
**Data** links work as usual. Startup notes about missing API keys can be ignored in this mode.

## 2. Run it live on that computer

You need your own keys. Create a file named `.env` in the repository root (it is git-ignored; never
commit it or paste it into the chat):

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
COGMESH_ALLOW_REMOTE_CONTENT=true
COGMESH_ALLOW_TRAINING_USE=true
```

(The two `ALLOW` lines are your statement that the file contents may be sent to those services.)

1. Create the sample project: `uv run python demo/make_shop_api.py ../shop-api`
2. Start the app with the project's check allowed to run (it runs on your computer, not isolated):
   `uv run cognitive-mesh serve --home demo-home --allow-unisolated-checks`
3. In the chat, add the folder `../shop-api` as a project, press **Confirm dependencies and start**,
   choose **Make a small change** and paste the request:

   > In shop/cart.py add a new class Discount with a code and a percent (0 to 100, reject anything
   > else), and a method apply(amount) that returns the discounted amount. Then let Cart take an
   > optional Discount and use it in total().

4. Review the diff and **Approve**. Models are not deterministic, so the drafts will differ from the
   recording.

Declare the project's check once, so a change is tested and so a pull request is allowed (the app
refuses to open one when no check passed). Use the same `--home` folder:

```bash
uv run cognitive-mesh workspace set-check --workspace demo-home/workspace.sqlite3 --by presenter shop-api -- python check_shop.py
```

## 3. Send the approved change to GitHub (pull request)

This sends the branch and its diff to GitHub, so it is a separate, deliberate step.

1. On GitHub, create an **empty** repository, for example `shop-api-demo` (no README).
2. Create a **fine-grained personal access token** for that one repository, with *Contents:
   Read and write*, *Pull requests: Read and write* (Metadata: Read is added for you). Put it in
   `.env` as `GITHUB_TOKEN=...`. Do not paste it anywhere else.
3. Create the sample with that remote, then publish `main` yourself (a pull request needs a base
   branch on GitHub):

   ```bash
   uv run python demo/make_shop_api.py ../shop-api --origin https://github.com/OWNER/shop-api-demo.git
   git -C ../shop-api push -u origin main
   ```

4. Check the setup before you present. Without `--online` nothing leaves the computer; with it, GitHub
   is asked whether the token can see and push to the repository:

   ```bash
   uv run python demo/check_github.py ../shop-api --online
   ```

5. Run the live example (section 2, with `--allow-unisolated-checks` and the check declared). After
   **Approve**, a second card, **Open a pull request?**, appears; **Push branch and open pull
   request** pushes the branch `cogmesh/change-...` and opens the pull request. Nothing is merged.

If the pull request is refused, the page says why: no token, the origin is not on github.com, the token
cannot see the repository, or no check passed for the approved commit.

## Not verified yet

The replay and the sample project are tested. A real push and pull request have not been run, and the
offline replay has been tried only on Windows.
