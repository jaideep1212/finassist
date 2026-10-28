-- One row per HOLDING: a scheme, in a folio, for a holder (sheet MFBase)
CREATE TABLE IF NOT EXISTS mf_base (
    fund_id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_name      varchar(120)  NOT NULL,
    scheme_code      integer,
    scheme_name      varchar(200)  NOT NULL,
    isin             char(12)      NOT NULL,
    scheme_category  varchar(100),
    folio_no         varchar(30)   NOT NULL,
    first_holder     varchar(50)   NOT NULL,
    loaded_at        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT uq_mf_base_holding UNIQUE (isin, folio_no, first_holder)
);

-- One row per TRANSACTION (sheet MFTxns)
CREATE TABLE IF NOT EXISTS mf_txns (
    txn_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fund_id       integer        NOT NULL REFERENCES mf_base (fund_id),
    txn_date      date           NOT NULL,
    txn_type      varchar(3)     NOT NULL
                  CHECK (txn_type IN ('PUR', 'RED', 'REI', 'DIV', 'REV')),
    amount        numeric(14, 2) NOT NULL,
    realized_amt  numeric(14, 2) NOT NULL,
    nav           numeric(14, 4) NOT NULL CHECK (nav >= 0),
    units         numeric(18, 4) NOT NULL CHECK (units >= 0),
    stt           numeric(12, 2) NOT NULL DEFAULT 0,
    tds           numeric(12, 2) NOT NULL DEFAULT 0,
    stamp_duty    numeric(12, 2) NOT NULL DEFAULT 0,
    loaded_at     timestamptz    NOT NULL DEFAULT now()
);

-- Indexes for the screen: AMC dropdown, scheme lookup, transactions of a holding
CREATE INDEX IF NOT EXISTS ix_mf_base_entity  ON mf_base (entity_name);
CREATE INDEX IF NOT EXISTS ix_mf_base_scheme  ON mf_base (isin);
CREATE INDEX IF NOT EXISTS ix_mf_txns_fund_dt ON mf_txns (fund_id, txn_date);