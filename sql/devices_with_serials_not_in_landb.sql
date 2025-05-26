-- sql/devices_not_in_landb.sql
SELECT ed.*
FROM eam_devices ed
WHERE NOT EXISTS (
    SELECT 1
    FROM landb_devices ld
    WHERE ld.equipmentno = ed.equipmentno
)
  AND ed.serialnumber IS NOT NULL
  AND TRIM(ed.serialnumber) <> '';
