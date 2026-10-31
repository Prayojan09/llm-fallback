-- Reserve against a monthly budget atomically using integer micro-USD.
local reserved = tonumber(redis.call('GET', KEYS[1]) or '0')
local used = tonumber(redis.call('GET', KEYS[2]) or '0')
local amount = tonumber(ARGV[1])
local budget = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('EXPIRE', KEYS[2], ttl)
if budget > 0 and used + reserved + amount > budget then
    return {0, used, reserved, math.ceil((used + reserved) / budget * 10000)}
end
redis.call('INCRBY', KEYS[1], amount)
redis.call('EXPIRE', KEYS[1], ttl)
local ratio = budget > 0 and math.ceil((used + reserved + amount) / budget * 10000) or 0
return {1, used, reserved + amount, ratio}
