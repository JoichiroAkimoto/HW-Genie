# Database Schema Critical Review (US-001)

## 1. Root Cause of Data Loss
The current implementation uses a single `JSON` column (`data`) in the `sessions` table to store all account-related settings and state.

In `src/python/hw_genie/core/repository.py`, the `save_data` method implements a full replacement strategy:
```python
if record:
    record.data = data
```
Because the entire JSON blob is overwritten by the provided dictionary, any data previously stored in the database that is not present in the new `data` dictionary is permanently lost. This "last-writer-wins" approach at the blob level, rather than the field level, is the direct cause of data loss.

## 2. Lack of Type Safety and Query Efficiency
- **Type Safety**: The `data` column is defined as `JSON`, and `SessionRepository.get_data` returns `Dict[str, Any]`. There is no schema validation or type enforcement for the contents of the JSON. This increases the risk of runtime errors due to missing keys or unexpected types.
- **Query Efficiency**: Since all settings are bundled in a JSON blob, it is impossible to perform efficient queries on specific settings without loading the entire record into memory. Indexing individual settings is not possible with the current schema, leading to $O(N)$ complexity for any search across accounts based on a setting value.

## 3. Scalability and Management Risks
- **Management Cost**: As the number of configuration items increases, the complexity of managing the JSON structure in code grows. Manual versioning or migration of JSON fields is error-prone compared to SQL schema migrations.
- **Performance Bottleneck**: As the blob grows in size, the I/O overhead for reading and writing the entire settings object for every small update will increase, potentially leading to performance degradation.
