# Library Native Plugin

MVP: create_book, tag_book, list_books, search_books. Uses a private SQLite DB and migrations.

## Enable in workspace

In `.joyhouse/config.yaml` (or equivalent) under `plugins.entries`, add the path to this directory, e.g.:

```yaml
plugins:
  entries:
    - path/to/examples/native-plugins/library
  allow:
    - library
```

Optional config per plugin:

```yaml
plugins:
  entries:
    - id: library
      path: path/to/examples/native-plugins/library
      config:
        data_dir: /optional/custom/dir  # default: ~/.joyhouse/plugins/library
```

## Tools (plugin API)

Used by the **Library App** and domain API (`/api/apps/library/*`); the default Agent does not call these directly (App-first). Tool names:

- `library.create_book` — title (required), isbn, tags[]
- `library.tag_book` — book_id, tag
- `library.list_books` — limit, offset
- `library.search_books` — q (title/isbn like), tag (filter by tag), limit
