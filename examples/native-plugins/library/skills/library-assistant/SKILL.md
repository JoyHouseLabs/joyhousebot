---
name: library-assistant
description: Recognize library intent and open the Library app; do not perform book operations in chat.
---

# library-assistant

Use this skill when the user wants to **manage books** (add a book, tag books, list books, search). Do **not** execute these operations in chat.

## What to do

1. **Recognize intent**: User said they want to add a book, list books, tag a book, or search books.
2. **Open the app**: Use the **open_app** tool with `app_id: "library"`. Optionally set `route` or `params` (e.g. `params: { "intent": "add_book" }`) so the Library app can show the right screen.
3. **Reply to user**: Say that you have opened the Library app and they can complete the action there (e.g. "已为你打开图书馆应用，你可以在应用里添加/查看/检索书籍。").

## Do not

- Do **not** use plugin.invoke or any tool to create books, list books, tag books, or search books. Those actions happen inside the Library app.
- Do **not** try to perform the user's request in the chat; only open the app.

## Examples

- "帮我加一本书" → open_app(app_id="library", route="/add", params={}) then reply "已打开图书馆应用，请在应用里添加书籍。"
- "列出我的书" / "我想搜书" → open_app(app_id="library", params={}) then reply "已打开图书馆应用，你可以在应用里查看或检索书籍。"
