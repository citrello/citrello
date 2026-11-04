-- Anonymous daily usage count from the Citrello app (no email, no IP address, no library content).
CREATE TABLE installs (
  id TEXT PRIMARY KEY,               -- random ID made by the app on that computer
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  days INTEGER NOT NULL DEFAULT 1,   -- number of different days it was used
  version TEXT,
  os TEXT,                           -- windows / mac / linux
  os_version TEXT,
  store INTEGER NOT NULL DEFAULT 0,  -- 1 = Microsoft Store version
  signed_in INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX installs_last_seen ON installs(last_seen);
