DO $$
DECLARE
    required_table text;
    id_type text;
    session_user_type text;
    account_user_type text;
BEGIN
    FOREACH required_table IN ARRAY ARRAY['user', 'session', 'account', 'verification'] LOOP
        IF to_regclass(format('auth.%I', required_table)) IS NULL THEN
            RAISE EXCEPTION 'Required Better Auth table is missing: %', required_table;
        END IF;
    END LOOP;
    SELECT data_type INTO id_type FROM information_schema.columns
    WHERE table_schema = 'auth' AND table_name = 'user' AND column_name = 'id';
    IF id_type IS DISTINCT FROM 'text' THEN
        RAISE EXCEPTION 'Expected auth.user.id to use the generated text type';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.conrelid = 'auth.user'::regclass AND c.contype = 'p'
        AND cardinality(c.conkey) = 1 AND a.attname = 'id'
    ) THEN
        RAISE EXCEPTION 'Expected auth.user.id to be the sole primary key';
    END IF;
    SELECT data_type INTO session_user_type FROM information_schema.columns
    WHERE table_schema = 'auth' AND table_name = 'session' AND column_name = 'userId';
    SELECT data_type INTO account_user_type FROM information_schema.columns
    WHERE table_schema = 'auth' AND table_name = 'account' AND column_name = 'userId';
    IF session_user_type IS DISTINCT FROM id_type OR account_user_type IS DISTINCT FROM id_type THEN
        RAISE EXCEPTION 'Better Auth user reference types do not match';
    END IF;
END $$;
