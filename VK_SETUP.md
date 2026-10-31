# Content Hub — VK publishing, v2.5.0

This update adds **manual, explicitly confirmed text-only** test posting to VK. It **does not** enable an automatic scheduler or translation. `VK_AUTO_PUBLISH=false` is reserved for a future release; the publisher does not use it to schedule posting.

## Configure

In the root `.env` file:

```dotenv
VK_GROUP_ID=123456789
VK_ACCESS_TOKEN=replace-with-your-real-access-token
VK_AUTO_PUBLISH=false
```

Do **not** commit `.env` or paste a token into chats. The numerical group ID is positive in `.env`; `wall.post` uses negative `owner_id` automatically.

## Run

```bash
cd ~/Projects/invest_blog_assistant
unzip -o ~/Downloads/content_hub_vk_v2_5_0_patch.zip
uv sync --extra dev
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python -m pytest -q
uv run python -m invest_blog.cli vk-test
# Only after reading the preview, explicitly publish one test post:
uv run python -m invest_blog.cli vk-test --publish --confirm PUBLISH
```

`vk-test` alone makes **zero network requests**. The confirmation command posts once. The `guid` is fixed to reduce duplicates if the same test is repeated. Remove the test post from your VK community after verifying it.

## VK access caveat

Community API keys might not be authorized for `wall.post` (wall permission may require a different VK OAuth token flow). If you get VK API error 15/27/etc., check token permissions and the currently supported wall posting authorization for your account. Do not grant unrelated permissions for messaging simply to make wall posting work.

No images, RSS translation, or automatic publication are included in this patch. `VK_AUTO_PUBLISH=false` is a **safety setting for future releases**, not a built-in VK parameter.
