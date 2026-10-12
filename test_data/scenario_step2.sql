-- Acceptance scenario, step 2 (run after the first FULL resolution):
--  (a) ERP updates a customer's phone      -> CDF update; entity id must stay stable; golden phone changes
--  (b) Web adds an ambiguous record          -> new record; expected to land in REVIEW or LINK
--  (c) CRM deletes its in-source duplicate   -> CDF delete; record leaves its entity
UPDATE {{catalog}}.rezolake2_fixtures.erp_customers
   SET tel = '+1 (214) 555 0100' WHERE cust_no = 'E-00001';
INSERT INTO {{catalog}}.rezolake2_fixtures.web_signups VALUES
   (900, 'j.', 'smith', NULL, NULL, 'Dallas');
DELETE FROM {{catalog}}.rezolake2_fixtures.crm_contacts WHERE contact_id = 'C0002';
