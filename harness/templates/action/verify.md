## Read-Back Verification

- After writing any file or artifact, read it back and confirm the content is
  what you intended, at the path the task asked for.
- Check the format the task specified, not just the content: exact filename,
  extension, encoding, field names, units.
- Verify each artifact as you produce it rather than batching all checks at the
  end, so a broken step is caught before later steps build on it.
- Never report an artifact as delivered on the strength of a successful write
  call alone.
