"""Global constants for the storage engine."""

PAGE_SIZE = 4096      # default page size in bytes (configurable per database, max 32768)
NO_PAGE = 0           # page 0 is the file header, so 0 doubles as the "null" page id
