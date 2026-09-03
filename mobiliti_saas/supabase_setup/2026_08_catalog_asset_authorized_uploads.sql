-- Allow trusted deployment tooling to insert immutable catalog assets while
-- keeping the service-role-only update/delete policy intact.
CREATE POLICY "catalog assets authorized inserts"
ON storage.objects
AS PERMISSIVE
FOR INSERT
TO anon
WITH CHECK (
    bucket_id = 'catalog-assets'
    AND name ~ '^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$'
    AND public.mobiliti_rest_authorized()
);
