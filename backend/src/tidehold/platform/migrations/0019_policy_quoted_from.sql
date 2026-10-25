-- Where a version's wording is quoted from, when it is a real operator's published text and
-- not the operator's own: {name, url, accessed}. Only the demo seed sets it (plan step 9 unit
-- 8; step9-design.md "Attribution"): the product contract allows a real operator's clause only
-- with its source URL and access date, and the guest page shows the clause. Written at
-- insert, so the version stays immutable.
alter table policy_versions
    add column quoted_from jsonb check (
        quoted_from is null or (
            jsonb_typeof(quoted_from) = 'object'
            and quoted_from ?& array['name', 'url', 'accessed']
        )
    );
