-- ===================================================
-- KindBridge - LOCAL RESET ONLY
-- File: db/reset_local.sql
--
-- Drops every KindBridge table, including dbo.event_store.
-- Projection rows and the event history are both destroyed.
-- Do not run this against a shared or remote database you need to keep.
-- Afterward, run db/schema.sql (or python db/init_db.py) to create empty tables.
-- ===================================================

IF OBJECT_ID(N'dbo.task_assignments', N'U') IS NOT NULL DROP TABLE dbo.task_assignments;
IF OBJECT_ID(N'dbo.volunteer_unavailability', N'U') IS NOT NULL DROP TABLE dbo.volunteer_unavailability;
IF OBJECT_ID(N'dbo.help_requests', N'U') IS NOT NULL DROP TABLE dbo.help_requests;
IF OBJECT_ID(N'dbo.request_series', N'U') IS NOT NULL DROP TABLE dbo.request_series;
IF OBJECT_ID(N'dbo.exemption_links', N'U') IS NOT NULL DROP TABLE dbo.exemption_links;
IF OBJECT_ID(N'dbo.requester_profiles', N'U') IS NOT NULL DROP TABLE dbo.requester_profiles;
IF OBJECT_ID(N'dbo.volunteer_profiles', N'U') IS NOT NULL DROP TABLE dbo.volunteer_profiles;
IF OBJECT_ID(N'dbo.users', N'U') IS NOT NULL DROP TABLE dbo.users;
IF OBJECT_ID(N'dbo.event_store', N'U') IS NOT NULL DROP TABLE dbo.event_store;
IF OBJECT_ID(N'dbo.login_attempts', N'U') IS NOT NULL DROP TABLE dbo.login_attempts;
