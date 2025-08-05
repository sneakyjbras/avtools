-- sql/devices_with_serials_in_landb.sql
SELECT ed.*
FROM eam_devices AS ed
INNER JOIN landb_devices AS ld
  ON ed.equipmentno = ld.equipmentno
WHERE ed.serialnumber IS NOT NULL
  AND TRIM(ed.serialnumber) <> '';
