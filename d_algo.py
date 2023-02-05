from algo import *
from collections.abc import Callable
import torch.distributed as dist


def flatten_grad(optimizer):
    t = []
    for _, param_group in enumerate(optimizer.param_groups):
        for p in param_group['params']:
            if p.grad is not None and p.requires_grad:
                t.append(p.grad.data.view(-1))
    return torch.cat(t)


class CReal_Sort(Sort):
    def __init__(self, rank, node_cnt: int, sort_maker: Callable[[], Sort]) -> None:
        super().__init__()
        self.node_cnt = node_cnt
        self.rank = rank
        self.sorter = sort_maker()

    def sort(self):
        return self.sorter.sort()

    def save_after_training(self, addr):
        pass


class CReal_GraB_OnlinePairBalance(CReal_Sort):
    def __init__(self, rank: int, args, n: int, m: int, d: int, device):
        assert m % 2 == 0, "online pair balance only supports even number"
        self.args = args
        self.rank = rank
        self.n = n
        self.m = m
        self.d = d
        self.device = device

        self.run_pair_diff_sum = torch.zeros(d, device=device)
        self.local_pair_cache = torch.zeros(d, device=device)
        self.next_orders = torch.arange(
            m, dtype=torch.int64).repeat(n).reshape(n, m)
        self.orders = self.next_orders.clone()
        self.left_ptr, self.right_ptr = 0, self.m - 1
        self.gathered_pair_cache = [torch.zeros(
            self.d, device=self.device) for _ in range(self.n)]
        self.args = args

    def all_gather_pair_cache(self):
        """
        Flatten local grad, all_gather with other nodes, and vstack all grads
        TODO: if slow, try append 0's and call all_reduce with ReduceOp SUM,
        and try other ways of storing the variable gathered_grad
        """
        self.gathered_pair_cache = [torch.zeros(
            self.d, device=self.device) for _ in range(self.n)]
        dist.all_gather(self.gathered_pair_cache,
                        self.local_pair_cache, async_op=False)
        self.gathered_pair_cache = torch.vstack(
            self.gathered_pair_cache).T  # (d, n)

    def reorder_online(self, i):
        # grad at even step subtract grad at odd step
        for j in range(self.n):
            plus_res, minus_res = self.run_pair_diff_sum + \
                self.gathered_pair_cache[:, j], self.run_pair_diff_sum - \
                self.gathered_pair_cache[:, j]
            if torch.norm(plus_res, p=2) <= torch.norm(minus_res, p=2):
                self.next_orders[j, self.left_ptr] = self.orders[j, i - 1]
                self.next_orders[j, self.right_ptr] = self.orders[j, i]
                self.run_pair_diff_sum = plus_res
            else:
                self.next_orders[j, self.right_ptr] = self.orders[j, i - 1]
                self.next_orders[j, self.left_ptr] = self.orders[j, i]
                self.run_pair_diff_sum = minus_res
        self.left_ptr += 1
        self.right_ptr -= 1

    def step(self, optimizer: torch.optim.Optimizer, batch_idx: int):
        if batch_idx % 2 == 0:
            # store gradients to use in next step
            self.local_pair_cache = flatten_grad(optimizer)
        else:
            # perform pair balance reorder online
            self.local_pair_cache -= flatten_grad(optimizer)
            self.all_gather_pair_cache()
            self.reorder_online(batch_idx)

    def sort(self):
        self.left_ptr = 0
        self.right_ptr = self.m - 1
        self.orders = self.next_orders
        self.next_orders = torch.zeros_like(self.next_orders)
        self.run_pair_diff_sum.zero_()
        return self.orders.clone()[self.rank]

    def save_after_training(self, addr):
        pass

class CReal_GraB_OnlinePairBalance_noGradAcc(CReal_Sort):
    def __init__(self, rank: int, args, n: int, m: int, d: int, device):
        assert m % 2 == 0, "online pair balance only supports even number"
        self.args = args
        self.rank = rank
        self.n = n
        self.m = m
        self.d = d
        self.device = device

        self.run_pair_diff_sum = torch.zeros(d, device=device)
        self.pair_cache = torch.zeros(d, n, device=device)
        self.next_orders = torch.arange(
            m, dtype=torch.int64).repeat(n).reshape(n, m)
        self.orders = self.next_orders.clone()
        self.left_ptr, self.right_ptr = 0, self.m - 1

    def all_gather_grad(self, optimizer):
        """
        Flatten local grad, all_gather with other nodes, and vstack all grads
        TODO: if slow, try append 0's and call all_reduce with ReduceOp SUM, 
        and try other ways of storing the variable gathered_grad
        """
        local_grad = flatten_grad(optimizer)
        self.gathered_grad = [torch.zeros(
            self.d, device=self.device) for _ in range(self.n)]
        dist.all_gather(self.gathered_grad, local_grad, async_op=False)
        self.gathered_grad = torch.vstack(self.gathered_grad).T  # (d, n)

    def reorder_online(self, i):
        # grad at even step subtract grad at odd step
        self.pair_cache -= self.gathered_grad
        for j in range(self.n):
            plus_res, minus_res = self.run_pair_diff_sum + \
                self.pair_cache[:, j], self.run_pair_diff_sum - \
                self.pair_cache[:, j]
            if torch.norm(plus_res, p=2) <= torch.norm(minus_res, p=2):
                self.next_orders[j, self.left_ptr] = self.orders[j, i - 1]
                self.next_orders[j, self.right_ptr] = self.orders[j, i]
                self.run_pair_diff_sum = plus_res
            else:
                self.next_orders[j, self.right_ptr] = self.orders[j, i - 1]
                self.next_orders[j, self.left_ptr] = self.orders[j, i]
                self.run_pair_diff_sum = minus_res

        self.left_ptr += 1
        self.right_ptr -= 1
        self.pair_cache.zero_()

    def step(self, optimizer: torch.optim.Optimizer, batch_idx: int):
        if batch_idx % 2 == 0:
            # store gradients to use in next step
            self.pair_cache += self.gathered_grad
        else:
            # perform pair balance reorder online
            self.reorder_online(batch_idx)

    def sort(self):
        self.pair_diff = 0
        self.left_ptr = 0
        self.right_ptr = self.m - 1
        self.orders = self.next_orders
        self.next_orders = torch.zeros_like(self.next_orders)
        return self.orders.clone()[self.rank]

    def save_after_training(self, addr):
        pass


class CReal_GraB_naive(CReal_Sort):
    def __init__(self, rank, args, n: int, m: int, d: int, device):
        def sort_maker(): return GraB(args, m, d, device=device)
        self.stats = {
            'sorter_cur_sum_fro': []
        }
        super().__init__(rank, n, sort_maker)

    def step(self, optimizer: torch.optim.Optimizer, batch_idx: int):
        cursum_timestep = None
        self.sorter.step(optimizer, batch_idx)
        cursum_timestep = torch.norm(self.sorter.cur_sum, 2)
        self.stats['sorter_cur_sum_fro'].append(cursum_timestep)

    def sort(self):
        return super().sort()

    def save_after_training(self, addr):
        # the node rank needs to be specified in the addr, o/w nodes will override each other
        torch.save(self.stats, f"{addr}-d-grab-sorter-stats.pt")


class CReal_RR(CReal_Sort):
    def __init__(self, rank, n, m):
        def sort_maker(): return RandomShuffle(m)
        super().__init__(rank, n, sort_maker)
        self.num_batches = m

    def step(self, *args, **kw):
        pass

    def sort(self, *args, **kw):
        return super().sort()

    def save_after_training(self, addr):
        pass


class CReal_WithR(CReal_Sort):
    def __init__(self, rank, node_cnt, num_batches):
        def sort_maker(): return WithReplacement_Sorter(num_batches)
        super().__init__(rank, node_cnt, sort_maker)
        self.num_batches = num_batches

    def step(self, *args, **kw):
        pass

    def sort(self, *args, **kw):
        return super().sort()

    def save_after_training(self, addr):
        pass


class CReal_ind_pairb(CReal_Sort):
    def __init__(self, rank: int, args, m: int, n: int, d: int, device=None):
        def sort_maker(): return PairBalance_Sorter(m, d, device=device)
        super().__init__(rank, n, sort_maker)

    # def reorder_online(self, grad_vecs, i):
    #     pass

    # def store_grad(self, grad_vecs):
    #         self.pair_cache += grad_vecs

    def step(self, optimizer, num_batch, *args, **kw):
        self.sorter.step(optimizer, num_batch)

    def sort(self, *args, **kw):
        return super().sort()

    def save_after_training(self, addr):
        pass
