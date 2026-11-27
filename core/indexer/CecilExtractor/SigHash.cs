using System;
using System.Security.Cryptography;
using System.Text;

namespace Uumc.Indexer.CecilExtractor;

public static class SigHash
{
    public static string Compute(string key)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(key.Trim());
        byte[] hash = SHA256.HashData(bytes);
        return Convert.ToHexString(hash).ToLowerInvariant().Substring(0, 16);
    }
}
