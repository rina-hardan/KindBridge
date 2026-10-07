-- ===================================================
-- KindBridge - MS SQL creation script (v1.1)
-- File: db/schema.sql
--
-- Creates missing tables, indexes, and constraints.
-- Does not DROP dbo.event_store or any projection table.
-- A local wipe (including the event store) is db/reset_local.sql.
--
-- Foreign keys do not use ON DELETE CASCADE. A user or request
-- delete must not erase projection rows; those rows change only
-- when an event is applied. Databases created by an older copy of
-- this file still have CASCADE until this script drops those
-- constraints and recreates them without it.
--
-- The filtered unique index requires ANSI_NULLS and
-- QUOTED_IDENTIFIER ON (the usual driver defaults).
-- ===================================================

-- 1. Event Store (Source of Truth - CQRS / Event Sourcing)
IF OBJECT_ID(N'dbo.event_store', N'U') IS NULL
CREATE TABLE dbo.event_store (
    seq BIGINT IDENTITY(1,1) NOT NULL,
    event_id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    aggregate_id UNIQUEIDENTIFIER NOT NULL,
    aggregate_type NVARCHAR(50) NOT NULL, -- User, HelpRequest, ExemptionLink
    event_type NVARCHAR(80) NOT NULL,
    payload_json NVARCHAR(MAX) NOT NULL,
    version INT NOT NULL,
    correlation_id UNIQUEIDENTIFIER NULL,
    causation_id UNIQUEIDENTIFIER NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_event_store_aggregate_version UNIQUE (aggregate_id, version)
);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_event_store_seq' AND object_id = OBJECT_ID(N'dbo.event_store'))
CREATE UNIQUE INDEX IX_event_store_seq ON dbo.event_store(seq);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_event_store_aggregate_id' AND object_id = OBJECT_ID(N'dbo.event_store'))
CREATE INDEX IX_event_store_aggregate_id ON dbo.event_store(aggregate_id);

-- 2. Users (Core Identity)
IF OBJECT_ID(N'dbo.users', N'U') IS NULL
CREATE TABLE dbo.users (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    email NVARCHAR(255) NOT NULL UNIQUE,
    password_hash NVARCHAR(255) NOT NULL,
    full_name NVARCHAR(200) NOT NULL,
    phone NVARCHAR(400) NOT NULL, -- Fernet ciphertext
    is_admin BIT NOT NULL DEFAULT 0,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- 3. Requester Profiles (1:1 Extension for Requesters)
IF OBJECT_ID(N'dbo.requester_profiles', N'U') IS NULL
CREATE TABLE dbo.requester_profiles (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    user_id UNIQUEIDENTIFIER NOT NULL,
    default_city NVARCHAR(100) NULL,
    default_address NVARCHAR(255) NULL, -- plaintext; hidden until ASSIGNED
    accessibility_notes NVARCHAR(500) NULL,
    emergency_contact_name NVARCHAR(200) NULL,
    emergency_contact_phone NVARCHAR(400) NULL, -- Fernet ciphertext
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_requester_profiles_user UNIQUE (user_id),
    CONSTRAINT FK_requester_profiles_user FOREIGN KEY (user_id) REFERENCES dbo.users(id)
);

-- 4. Volunteer Profiles (1:1 Extension for Volunteers)
IF OBJECT_ID(N'dbo.volunteer_profiles', N'U') IS NULL
CREATE TABLE dbo.volunteer_profiles (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    user_id UNIQUEIDENTIFIER NOT NULL,
    is_enabled BIT NOT NULL DEFAULT 1,
    primary_city NVARCHAR(100) NOT NULL,
    has_vehicle BIT NOT NULL DEFAULT 0,
    skills_json NVARCHAR(MAX) NOT NULL,
    experience NVARCHAR(MAX) NOT NULL,
    base_frequency NVARCHAR(30) NOT NULL, -- WEEKLY, BIWEEKLY, MONTHLY, ON_DEMAND
    availability_status NVARCHAR(30) NOT NULL DEFAULT 'AVAILABLE', -- AVAILABLE, INACTIVE
    max_active_tasks INT NOT NULL DEFAULT 1,
    max_parallel_tasks INT NOT NULL DEFAULT 2,
    current_active_tasks INT NOT NULL DEFAULT 0,
    current_parallel_tasks INT NOT NULL DEFAULT 0,
    CONSTRAINT UQ_volunteer_profiles_user UNIQUE (user_id),
    CONSTRAINT FK_volunteer_profiles_user FOREIGN KEY (user_id) REFERENCES dbo.users(id)
);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_volunteer_profiles_primary_city' AND object_id = OBJECT_ID(N'dbo.volunteer_profiles'))
CREATE INDEX IX_volunteer_profiles_primary_city ON dbo.volunteer_profiles(primary_city);

-- 5. Volunteer Unavailability Periods
IF OBJECT_ID(N'dbo.volunteer_unavailability', N'U') IS NULL
CREATE TABLE dbo.volunteer_unavailability (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    volunteer_id UNIQUEIDENTIFIER NOT NULL,
    from_date DATE NOT NULL,
    until_date DATE NOT NULL,
    reason NVARCHAR(200) NULL,
    is_cancelled BIT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT FK_volunteer_unavailability_profile FOREIGN KEY (volunteer_id) REFERENCES dbo.volunteer_profiles(id),
    CONSTRAINT CK_volunteer_unavailability_dates CHECK (until_date >= from_date)
);

-- 6. Request Series (Deferred / Reserved for Recurring Requests)
IF OBJECT_ID(N'dbo.request_series', N'U') IS NULL
CREATE TABLE dbo.request_series (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    requester_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    recurrence NVARCHAR(20) NOT NULL, -- WEEKLY, BIWEEKLY, MONTHLY
    start_date DATE NOT NULL,
    end_date DATE NULL,
    preferred_volunteer_id UNIQUEIDENTIFIER NULL FOREIGN KEY REFERENCES dbo.volunteer_profiles(id),
    is_active BIT NOT NULL DEFAULT 1
);

-- 7. Help Requests
IF OBJECT_ID(N'dbo.help_requests', N'U') IS NULL
CREATE TABLE dbo.help_requests (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    requester_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    series_id UNIQUEIDENTIFIER NULL FOREIGN KEY REFERENCES dbo.request_series(id),
    city NVARCHAR(100) NOT NULL,
    address NVARCHAR(255) NOT NULL, -- plaintext; hidden from the volunteer until ASSIGNED
    category NVARCHAR(50) NOT NULL,
    resource_type NVARCHAR(30) NOT NULL, -- PHYSICAL_PRESENCE, EQUIPMENT_LOAN, FLEXIBLE_REMOTE
    description NVARCHAR(MAX) NOT NULL,
    urgency NVARCHAR(20) NOT NULL, -- LOW, NORMAL, HIGH, EMERGENCY
    preferred_date DATE NULL,
    preferred_time_from TIME NULL,
    preferred_time_to TIME NULL,
    estimated_duration_min INT NULL,
    required_skills_json NVARCHAR(MAX) NOT NULL,
    requires_vehicle BIT NOT NULL DEFAULT 0,
    concurrency_type NVARCHAR(20) NOT NULL DEFAULT 'UNKNOWN', -- EXCLUSIVE, PARALLEL_OK, UNKNOWN
    status NVARCHAR(30) NOT NULL, -- PENDING_REVIEW, MATCH_PROPOSED, NO_MATCH, ASSIGNED, COMPLETED, CANCELLED
    match_attempt INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_help_requests_status' AND object_id = OBJECT_ID(N'dbo.help_requests'))
CREATE INDEX IX_help_requests_status ON dbo.help_requests(status);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_help_requests_city' AND object_id = OBJECT_ID(N'dbo.help_requests'))
CREATE INDEX IX_help_requests_city ON dbo.help_requests(city);

-- 8. Task Assignments
IF OBJECT_ID(N'dbo.task_assignments', N'U') IS NULL
CREATE TABLE dbo.task_assignments (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    request_id UNIQUEIDENTIFIER NOT NULL,
    volunteer_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.volunteer_profiles(id),
    ai_score DECIMAL(5,2) NULL,
    ai_rationale NVARCHAR(500) NULL,
    rank_in_batch INT NULL,
    match_attempt INT NOT NULL,
    approved_by UNIQUEIDENTIFIER NULL FOREIGN KEY REFERENCES dbo.users(id),
    status NVARCHAR(30) NOT NULL, -- PROPOSED, ASSIGNED, DECLINED, COMPLETED, SUPERSEDED
    decline_reason NVARCHAR(500) NULL,
    override_reason NVARCHAR(500) NULL,
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT FK_task_assignments_request FOREIGN KEY (request_id) REFERENCES dbo.help_requests(id)
);

-- At most one ASSIGNED or COMPLETED row per request. Other statuses may repeat.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'UQ_task_assignments_request_assigned_or_completed'
      AND object_id = OBJECT_ID(N'dbo.task_assignments')
)
CREATE UNIQUE INDEX UQ_task_assignments_request_assigned_or_completed
    ON dbo.task_assignments(request_id)
    WHERE status IN (N'ASSIGNED', N'COMPLETED');

-- 9. Exemption Links (Mutual Exclusions)
IF OBJECT_ID(N'dbo.exemption_links', N'U') IS NULL
CREATE TABLE dbo.exemption_links (
    volunteer_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    requester_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    created_by UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    reason NVARCHAR(300) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    PRIMARY KEY (volunteer_id, requester_id)
);

-- 10. Login Attempts (Rate Limiting & Security Audit)
IF OBJECT_ID(N'dbo.login_attempts', N'U') IS NULL
CREATE TABLE dbo.login_attempts (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    email NVARCHAR(255) NOT NULL,
    attempted_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    succeeded BIT NOT NULL
);

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_login_attempts_email_attempted_at'
      AND object_id = OBJECT_ID(N'dbo.login_attempts')
)
CREATE INDEX IX_login_attempts_email_attempted_at ON dbo.login_attempts(email, attempted_at);

-- Drop ON DELETE CASCADE left by an older schema, then ensure a NO ACTION foreign key.
-- The string below is one batch; semicolons inside it are not statement separators.
EXEC sp_executesql N'
DECLARE @sql nvarchar(max) = N'''';
SELECT @sql += N''ALTER TABLE ''
    + QUOTENAME(OBJECT_SCHEMA_NAME(fk.parent_object_id)) + N''.''
    + QUOTENAME(OBJECT_NAME(fk.parent_object_id))
    + N'' DROP CONSTRAINT '' + QUOTENAME(fk.name) + N'';''
FROM sys.foreign_keys AS fk
WHERE fk.delete_referential_action = 1
  AND (
        (OBJECT_NAME(fk.parent_object_id) = N''requester_profiles'' AND OBJECT_NAME(fk.referenced_object_id) = N''users'')
     OR (OBJECT_NAME(fk.parent_object_id) = N''volunteer_profiles'' AND OBJECT_NAME(fk.referenced_object_id) = N''users'')
     OR (OBJECT_NAME(fk.parent_object_id) = N''volunteer_unavailability'' AND OBJECT_NAME(fk.referenced_object_id) = N''volunteer_profiles'')
     OR (OBJECT_NAME(fk.parent_object_id) = N''task_assignments'' AND OBJECT_NAME(fk.referenced_object_id) = N''help_requests'')
  );
IF @sql <> N'''' EXEC sp_executesql @sql;
';

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_requester_profiles_user')
ALTER TABLE dbo.requester_profiles ADD CONSTRAINT FK_requester_profiles_user FOREIGN KEY (user_id) REFERENCES dbo.users(id);

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_volunteer_profiles_user')
ALTER TABLE dbo.volunteer_profiles ADD CONSTRAINT FK_volunteer_profiles_user FOREIGN KEY (user_id) REFERENCES dbo.users(id);

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_volunteer_unavailability_profile')
ALTER TABLE dbo.volunteer_unavailability ADD CONSTRAINT FK_volunteer_unavailability_profile FOREIGN KEY (volunteer_id) REFERENCES dbo.volunteer_profiles(id);

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_task_assignments_request')
ALTER TABLE dbo.task_assignments ADD CONSTRAINT FK_task_assignments_request FOREIGN KEY (request_id) REFERENCES dbo.help_requests(id);

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = N'CK_volunteer_unavailability_dates'
      AND parent_object_id = OBJECT_ID(N'dbo.volunteer_unavailability')
)
ALTER TABLE dbo.volunteer_unavailability ADD CONSTRAINT CK_volunteer_unavailability_dates CHECK (until_date >= from_date);
