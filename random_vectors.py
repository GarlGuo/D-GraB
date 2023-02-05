import numpy as np
import argparse


parser = argparse.ArgumentParser(description="simulated herding bound values")
parser.add_argument("--d", type=int, required=True, help="worker model params")
parser.add_argument("--m", type=int, required=True,
                    help="number of examples per worker")
parser.add_argument("--n", type=int, required=True, help="number of worker")
parser.add_argument("--r", type=int, default=5, help="number of trials")
parser.add_argument("--round", type=int, default=15,
                    help="number of reorder rounds")
args = parser.parse_args()

print(vars(args))
m, n, d = args.m, args.n, args.d
mn = m * n

rounds = args.round


def herding_bound(vecs):
    return np.maximum.accumulate(np.linalg.norm(np.cumsum(vecs, 0), ord=float('inf'), axis=1))[-1]


def parallel_herding_bound(vecs):
    # vecs: m, n, d
    return np.maximum.accumulate(np.linalg.norm(np.cumsum(np.sum(vecs, axis=1), axis=0), ord=float('inf'), axis=1))[-1]


def sign_reorder(signs, single_vecs):
    next_vecs = np.empty_like(single_vecs)

    pos_half = np.where(signs == +1)
    pos_half_size = len(pos_half[0])
    next_vecs[:pos_half_size] = single_vecs[pos_half]

    neg_half = np.where(signs == -1)
    neg_half_size = len(neg_half[0])
    next_vecs[pos_half_size:] = single_vecs[neg_half][::-1]

    assert pos_half_size + neg_half_size == len(single_vecs)

    return next_vecs


def Balance(single_vecs):
    # single_vecs: mn, d
    run_sum = np.zeros_like(single_vecs[0])
    signs = np.zeros((len(single_vecs),), dtype=np.int8)
    for i, vec in enumerate(single_vecs):
        if np.linalg.norm(run_sum + vec, ord=2) <= np.linalg.norm(run_sum - vec, ord=2):
            signs[i] = +1
            run_sum += vec
        else:
            signs[i] = -1
            run_sum -= vec
    return signs


def Centralized_Balance_multiround(single_vecs, round):
    herding_bounds = []
    # single_vecs: mn, d
    herding_bounds.append(herding_bound(single_vecs))  # zero round
    for _ in range(round):
        signs = Balance(single_vecs)
        single_vecs = sign_reorder(signs, single_vecs)
        herding_bounds.append(herding_bound(single_vecs))
    return herding_bounds


def Independent_Balance(vecs):
    # vecs: m, n, d
    m, n, d = vecs.shape
    signs = np.zeros((m, n), dtype=np.int8)
    for j in range(n):
        signs_j = Balance(vecs[:, j, :])
        signs[:, j] = signs_j
    return signs


def Independent_reorder(signs, vecs):
    n = vecs.shape[1]
    next_vecs = np.empty_like(vecs)
    for j in range(n):
        next_vecs[:, j, :] = sign_reorder(signs[:, j], vecs[:, j, :])
    return next_vecs


def Independent_Balance_multiround(vecs, round):
    herding_bounds = []
    # vecs: m, n, d
    herding_bounds.append(parallel_herding_bound(vecs))  # zero round
    for _ in range(round):
        signs = Independent_Balance(vecs)
        vecs = Independent_reorder(signs, vecs)
        herding_bounds.append(parallel_herding_bound(vecs))
    return herding_bounds


def D_GraB_and_reorder(vecs):
    # vecs: m, n, d
    m, n, d = vecs.shape
    mn = m * n
    run_sum = np.zeros((d,))

    next_epoch_vecs = np.empty_like(vecs)
    left, right = 0, m - 1

    for i in range(0, m, 2):
        pair_diff = vecs[i, :, :] - vecs[i + 1, :, :]  # n, d
        for j in range(n):
            if np.linalg.norm(run_sum + pair_diff[j], ord=2) <= np.linalg.norm(run_sum - pair_diff[j], ord=2):
                next_epoch_vecs[left, j] = vecs[i, j]  # +1
                next_epoch_vecs[right, j] = vecs[i + 1, j]  # -1
                run_sum += pair_diff[j]
            else:
                next_epoch_vecs[right, j] = vecs[i, j]  # -1
                next_epoch_vecs[left, j] = vecs[i + 1, j]  # +1
                run_sum -= pair_diff[j]
        left += 1
        right -= 1

    return next_epoch_vecs


def D_GraB_multiround(vecs, round):
    herding_bounds = []
    # vecs: m, n, d
    herding_bounds.append(parallel_herding_bound(vecs))
    for _ in range(round):
        vecs = D_GraB_and_reorder(vecs)
        herding_bounds.append(parallel_herding_bound(vecs))
    return herding_bounds


def Centralized_PairBalance(vecs):
    # vecs: mn d
    mn, d = vecs.shape
    run_sum = np.zeros((d,))

    next_epoch_vecs = np.empty_like(vecs)
    left, right = 0, mn - 1

    for i in range(0, mn, 2):
        pair_diff = vecs[i, :] - vecs[i + 1, :]  # d
        if np.linalg.norm(run_sum + pair_diff, ord=2) <= np.linalg.norm(run_sum - pair_diff, ord=2):
            next_epoch_vecs[left] = vecs[i]  # +1
            next_epoch_vecs[right] = vecs[i + 1]  # -1
            run_sum += pair_diff
        else:
            next_epoch_vecs[right] = vecs[i]  # -1
            next_epoch_vecs[left] = vecs[i + 1]  # +1
            run_sum -= pair_diff
        left += 1
        right -= 1

    return next_epoch_vecs


def Centralized_PairBalance_multiround(vecs, round):
    herding_bounds = []
    # vecs: mn, d
    herding_bounds.append(herding_bound(vecs))
    for _ in range(round):
        vecs = Centralized_PairBalance(vecs)
        herding_bounds.append(herding_bound(vecs))
    return herding_bounds


def Independent_PairBalance_multiround(vecs, round):
    herding_bounds = []
    # vecs: m, n, d
    herding_bounds.append(parallel_herding_bound(vecs))  # zero round
    for _ in range(round):
        for i in range(n):
            vecs[:, i, :] = Centralized_PairBalance(vecs[:, i, :])
        herding_bounds.append(parallel_herding_bound(vecs))
    return herding_bounds


C_B_herding_values = []
I_B_herding_values = []
D_GraB_herding_values = []
C_PB_herding_values = []
I_PB_herding_values = []

for i in range(args.r):
    decentralized_vecs = np.random.RandomState(i).randn(mn, d)
    decentralized_vecs = decentralized_vecs / np.linalg.norm(decentralized_vecs, ord=2, axis=1).reshape(mn, 1)
    decentralized_vecs = decentralized_vecs.reshape(m, n, d)
    decentralized_vecs -= np.expand_dims(decentralized_vecs.mean(axis=0), 0)

    centralized_vecs = decentralized_vecs.reshape(mn, d)
    C_B_herding_values.append(
        Centralized_Balance_multiround(centralized_vecs, rounds))
    I_B_herding_values.append(
        Independent_Balance_multiround(decentralized_vecs, rounds))
    D_GraB_herding_values.append(
        D_GraB_multiround(decentralized_vecs, rounds))
    C_PB_herding_values.append(
        Centralized_PairBalance_multiround(centralized_vecs, rounds))
    I_PB_herding_values.append(
        Independent_PairBalance_multiround(decentralized_vecs, rounds)
    )
    del decentralized_vecs
