-- Atomically refill and check both team buckets, then debit both or neither.
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000

local function refill(key, capacity, rate)
    local values = redis.call('HMGET', key, 'tokens', 'updated_at')
    local tokens = tonumber(values[1]) or capacity
    local updated_at = tonumber(values[2]) or now
    tokens = math.min(capacity, tokens + math.max(0, now - updated_at) * rate)
    return tokens
end

local request_rate = tonumber(ARGV[1])
local request_capacity = tonumber(ARGV[2])
local token_rate = tonumber(ARGV[3])
local token_capacity = tonumber(ARGV[4])
local token_cost = tonumber(ARGV[5])
local ttl_ms = tonumber(ARGV[6])

local request_tokens = refill(KEYS[1], request_capacity, request_rate)
local budget_tokens = refill(KEYS[2], token_capacity, token_rate)
local allowed = request_tokens >= 1 and budget_tokens >= token_cost
local retry_after = 0

if allowed then
    request_tokens = request_tokens - 1
    budget_tokens = budget_tokens - token_cost
else
    if request_tokens < 1 then
        retry_after = math.max(retry_after, (1 - request_tokens) / request_rate)
    end
    if budget_tokens < token_cost then
        retry_after = math.max(retry_after, (token_cost - budget_tokens) / token_rate)
    end
end

redis.call('HSET', KEYS[1], 'tokens', request_tokens, 'updated_at', now)
redis.call('HSET', KEYS[2], 'tokens', budget_tokens, 'updated_at', now)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
redis.call('PEXPIRE', KEYS[2], ttl_ms)

local request_reset = math.ceil((request_capacity - request_tokens) / request_rate)
local token_reset = math.ceil((token_capacity - budget_tokens) / token_rate)
return {
    allowed and 1 or 0,
    math.floor(request_tokens),
    math.floor(budget_tokens),
    request_reset,
    token_reset,
    math.ceil(retry_after)
}
